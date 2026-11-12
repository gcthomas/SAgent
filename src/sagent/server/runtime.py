"""会话级 Agent 运行时。

每个 Web 会话持有独立的 ContextManager、SessionManager、Web 审批器、
权限执行器、工具注册表与执行引擎，实现会话之间的上下文、审批白名单
与运行状态隔离；同一会话同一时刻只允许一个进行中的 run，不同会话可并行。

审批器 → 权限执行器 → 工具注册表三者必须按会话独立构建：审批器保存着
always 白名单与 pending 记录，共享会导致跨会话串扰。为此本模块在最小范围
内复制 CLI 的装配顺序，而不重构 CLI 代码。
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..config.models import AppConfig, PermissionConfig
from ..context.context_manager import ContextManager
from ..core.agent_engine import AgentEngine
from ..core.cancellation import CancelToken
from ..core.events import EventHandler
from ..llm.client import LLMClient
from ..memory import MemoryManager
from ..observability import get_logger
from ..permissions import (
    Decision,
    PermissionEnforcer,
    PermissionRule,
    build_default_policy,
    parse_rule,
)
from ..session.manager import SessionManager
from ..session.store import SessionStore
from ..skills import SkillLoader
from ..tools import AddMemoryTool, RemoveMemoryTool, ReplaceMemoryTool, build_default_registry
from ..tools.mcp import MCPSessionManager, build_mcp_providers
from ..tools.registry import ToolRegistry
from .approval import WebApprovalHandler

logger = get_logger(__name__)


class SessionNotFoundError(Exception):
    """会话不存在。"""


class SessionBusyError(Exception):
    """同一会话已有进行中的运行。"""


class RunNotFoundError(Exception):
    """运行 id 不存在。"""


class RunFinishedError(Exception):
    """运行已结束（完成、失败或已取消）。"""


# 已结束 run id 的保留上限：仅用于区分 404 与 409，保留最近若干个足够覆盖重试窗口
_MAX_FINISHED_RUN_IDS = 64


@dataclass
class ActiveRun:
    """会话内一次进行中（或刚结束）的运行。

    loop 与 bridge 由上层事件编排在 start_run 之后补充赋值，本模块不使用，
    因此类型保持宽松，避免与事件桥模块形成循环依赖。
    """

    run_id: str
    cancel_token: CancelToken
    engine: AgentEngine
    started_at: float
    status: str = "running"
    loop: asyncio.AbstractEventLoop | None = None
    bridge: Any = None


def _build_session_permission(
    permission_config: PermissionConfig, approval: WebApprovalHandler
) -> PermissionEnforcer | None:
    """按 CLI 的规则组装顺序构建会话级权限执行器。

    规则按 ask -> allow -> deny 追加，借助 last-match-wins 实现
    deny > allow > ask 的固定跨列表优先级；与 CLI 的唯一差异是审批器
    换成该会话的 WebApprovalHandler。

    参数:
        permission_config: 权限控制配置。
        approval: 该会话的 Web 审批器。

    返回:
        权限执行器；权限控制未启用时返回 None。
    """
    if not permission_config.enabled:
        return None
    rules: list[PermissionRule] = []
    for text in permission_config.ask:
        rules.append(parse_rule(text, Decision.ASK))
    for text in permission_config.allow:
        rules.append(parse_rule(text, Decision.ALLOW))
    for text in permission_config.deny:
        rules.append(parse_rule(text, Decision.DENY))
    policy = build_default_policy(rules)
    return PermissionEnforcer(
        policy,
        approval,
        timeout=permission_config.ask_timeout,
        non_interactive=permission_config.non_interactive,
    )


class SessionRuntime:
    """单个会话的独立运行时。"""

    def __init__(
        self,
        session_id: str,
        *,
        config: AppConfig,
        store: SessionStore,
        llm: LLMClient,
        memory_manager: MemoryManager | None = None,
        skill_loader: SkillLoader | None = None,
        mcp_session_manager: MCPSessionManager | None = None,
    ) -> None:
        """构建会话级对象并还原已持久化的会话上下文。

        参数:
            session_id: 会话 id。
            config: 应用总配置。
            store: 会话持久化存储。
            llm: LLM 客户端（共享）。
            memory_manager: 已构建的记忆管理器，未启用时为 None。
            skill_loader: 已构建的 Skill 正文加载器，未启用时为 None。
            mcp_session_manager: 已启动的 MCP 会话管理器，未启用时为 None。
        """
        self._session_id = session_id
        self._config = config
        self._store = store
        self._llm = llm
        self._lock = threading.Lock()
        self._active_run: ActiveRun | None = None
        # 已结束的 run_id，用于区分"重复结束"（409）与"未知运行"（404）；
        # 数量上限见 _MAX_FINISHED_RUN_IDS
        self._finished_run_ids: set[str] = set()

        # 上下文管理器：构造参数与 CLI 完全一致
        summary_llm = (
            LLMClient(config.context.summary_llm)
            if config.context.enable_summary and config.context.summary_llm is not None
            else None
        )
        self._context_manager = ContextManager(
            config.context,
            summary_llm=summary_llm,
            summary_model=config.context.summary_llm.model
            if config.context.summary_llm is not None
            else "",
        )
        # SessionManager 构造时会向 ContextManager 注册压缩回调
        self._session_manager = SessionManager(store, self._context_manager)

        # 每会话一套审批器、权限执行器与工具注册表
        self._approval = WebApprovalHandler(
            timeout=config.permissions.ask_timeout,
            non_interactive=config.permissions.non_interactive,
        )
        self._enforcer = _build_session_permission(config.permissions, self._approval)
        self._registry = build_default_registry(permission=self._enforcer)

        # 记忆工具与 CLI 一致，按需显式注册
        if memory_manager is not None:
            self._registry.register(AddMemoryTool(memory_manager))
            self._registry.register(ReplaceMemoryTool(memory_manager))
            self._registry.register(RemoveMemoryTool(memory_manager))

        # Skill 工具在本地导入，避免模块级循环依赖（与 CLI 一致）
        if skill_loader is not None:
            from ..tools.skill_tool import LoadSkillTool, ReadSkillResourceTool

            self._registry.register(LoadSkillTool(skill_loader))
            self._registry.register(ReadSkillResourceTool(skill_loader))

        if mcp_session_manager is not None:
            for provider in build_mcp_providers(config.mcp, mcp_session_manager):
                self._registry.register_provider(provider)

        # 启动时还原工作上下文与活动计划
        self._session_manager.switch_session(session_id)
        logger.info(
            "会话运行时已创建",
            extra={"event": "session_runtime_created", "session_id": session_id},
        )

    # --- 只读属性 ---

    @property
    def session_id(self) -> str:
        """会话 id。"""
        return self._session_id

    @property
    def approval(self) -> WebApprovalHandler:
        """该会话的 Web 审批器。"""
        return self._approval

    @property
    def context_manager(self) -> ContextManager:
        """该会话的上下文管理器。"""
        return self._context_manager

    @property
    def session_manager(self) -> SessionManager:
        """该会话的会话管理器。"""
        return self._session_manager

    @property
    def tool_registry(self) -> ToolRegistry:
        """该会话的工具注册表。"""
        return self._registry

    @property
    def active_run(self) -> ActiveRun | None:
        """当前进行中的运行；无则为 None。"""
        with self._lock:
            return self._active_run

    @property
    def finished_run_ids(self) -> frozenset[str]:
        """已结束运行的 id 快照，供注册表区分 404 与 409。"""
        with self._lock:
            return frozenset(self._finished_run_ids)

    # --- 引擎与运行生命周期 ---

    def build_engine(
        self, cancel_token: CancelToken, *, on_event: EventHandler | None = None
    ) -> AgentEngine:
        """构建绑定指定取消令牌与会话上下文的执行引擎。

        取消令牌在引擎构造时固定，因此每次运行都必须创建新的引擎实例；
        ContextManager 与 SessionManager 在整个会话内复用。

        参数:
            cancel_token: 本次运行的取消令牌。
            on_event: 事件回调。

        返回:
            AgentEngine 实例。
        """
        engine = AgentEngine(
            self._llm,
            self._registry,
            self._config.agent,
            planning_config=self._config.planning,
            on_event=on_event,
            context_manager=self._context_manager,
            cancel_token=cancel_token,
        )
        # 与 CLI 一致：让引擎把活动计划写回会话管理器
        engine.set_session_manager(self._session_manager)
        return engine

    def begin_run(
        self,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        on_event: EventHandler | None = None,
    ) -> ActiveRun:
        """开始一次运行；同一会话已有未结束运行时拒绝。

        参数:
            loop: 事件循环，供事件桥从工作线程安全投递事件。
            on_event: 本次运行的事件回调，同时注入审批器。

        返回:
            新建的 ActiveRun。

        异常:
            SessionBusyError: 已有未结束的运行。
        """
        with self._lock:
            if self._active_run is not None:
                raise SessionBusyError(f"会话 {self._session_id} 正在执行，请等待当前运行结束")
            cancel_token = CancelToken()
            engine = self.build_engine(cancel_token, on_event=on_event)
            run = ActiveRun(
                run_id=uuid.uuid4().hex,
                cancel_token=cancel_token,
                engine=engine,
                started_at=time.monotonic(),
                loop=loop,
            )
            self._approval.set_event_handler(on_event)
            self._active_run = run
            logger.info(
                "运行开始",
                extra={
                    "event": "run_started",
                    "session_id": self._session_id,
                    "run_id": run.run_id,
                },
            )
            return run

    def finish_run(self, run_id: str, status: str) -> None:
        """结束运行并记录终态，仅当 run_id 与当前运行匹配时清理。

        参数:
            run_id: 运行 id。
            status: 终态（completed / failed / cancelled）。

        异常:
            RunNotFoundError: run_id 未知。
            RunFinishedError: 运行已结束。
        """
        with self._lock:
            active = self._active_run
            if active is None or active.run_id != run_id:
                if run_id in self._finished_run_ids:
                    raise RunFinishedError(f"运行 {run_id} 已结束")
                raise RunNotFoundError(f"运行 {run_id} 不存在")
            active.status = status
            self._active_run = None
            # 限制已结束 run id 的保留数量，避免长会话内存无界增长；
            # 集合仅用于区分 404 与 409，保留最近若干个足够覆盖重试窗口。
            self._finished_run_ids.add(run_id)
            if len(self._finished_run_ids) > _MAX_FINISHED_RUN_IDS:
                for stale in sorted(self._finished_run_ids)[:-_MAX_FINISHED_RUN_IDS]:
                    self._finished_run_ids.discard(stale)
            logger.info(
                "运行结束",
                extra={
                    "event": "run_finished",
                    "session_id": self._session_id,
                    "run_id": run_id,
                    "status": status,
                },
            )

    def cancel_run(self, run_id: str) -> None:
        """协作式取消运行，并拒绝该会话内所有等待中的审批。

        参数:
            run_id: 运行 id。

        异常:
            RunNotFoundError: run_id 未知。
            RunFinishedError: 运行已结束或已取消。
        """
        with self._lock:
            active = self._active_run
            if active is None or active.run_id != run_id:
                if run_id in self._finished_run_ids:
                    raise RunFinishedError(f"运行 {run_id} 已结束")
                raise RunNotFoundError(f"运行 {run_id} 不存在")
            if active.cancel_token.is_cancelled():
                raise RunFinishedError(f"运行 {run_id} 已取消")
            active.cancel_token.cancel()
        # 审批事件会在锁外发出，避免与等待线程互相阻塞
        self._approval.cancel_pending("运行已取消")
        logger.info(
            "运行已取消",
            extra={
                "event": "run_cancelled",
                "session_id": self._session_id,
                "run_id": run_id,
            },
        )


class RuntimeRegistry:
    """会话 id 到运行时的注册表；创建、释放与删除均在锁内保证幂等。"""

    def __init__(
        self,
        *,
        config: AppConfig,
        store: SessionStore,
        llm: LLMClient,
        memory_manager: MemoryManager | None = None,
        skill_loader: SkillLoader | None = None,
        mcp_session_manager: MCPSessionManager | None = None,
    ) -> None:
        """保存共享依赖，运行时按会话惰性创建。

        参数:
            config: 应用总配置。
            store: 会话持久化存储。
            llm: LLM 客户端（共享）。
            memory_manager: 记忆管理器，未启用时为 None。
            skill_loader: Skill 正文加载器，未启用时为 None。
            mcp_session_manager: MCP 会话管理器，未启用时为 None。
        """
        self._config = config
        self._store = store
        self._llm = llm
        self._memory_manager = memory_manager
        self._skill_loader = skill_loader
        self._mcp_session_manager = mcp_session_manager
        self._lock = threading.Lock()
        self._runtimes: dict[str, SessionRuntime] = {}

    def get_or_create(self, session_id: str) -> SessionRuntime:
        """返回会话运行时，首次访问时创建。

        参数:
            session_id: 会话 id。

        返回:
            会话运行时。

        异常:
            SessionNotFoundError: 会话记录不存在。
        """
        with self._lock:
            runtime = self._runtimes.get(session_id)
            if runtime is not None:
                return runtime
            if self._store.get_session(session_id) is None:
                raise SessionNotFoundError(f"会话 {session_id} 不存在")
            runtime = SessionRuntime(
                session_id,
                config=self._config,
                store=self._store,
                llm=self._llm,
                memory_manager=self._memory_manager,
                skill_loader=self._skill_loader,
                mcp_session_manager=self._mcp_session_manager,
            )
            self._runtimes[session_id] = runtime
            return runtime

    def get(self, session_id: str) -> SessionRuntime | None:
        """返回已创建的会话运行时；不存在返回 None。"""
        with self._lock:
            return self._runtimes.get(session_id)

    def find_by_run_id(self, run_id: str) -> SessionRuntime | None:
        """按运行 id 查找所属会话运行时。

        先匹配进行中的运行；再匹配该运行时已结束的运行 id，使取消接口能够
        区分「运行不存在」（404）与「运行已结束」（409）。查找在注册表锁内
        取得运行时快照后进行，避免持有注册表锁调用运行时锁造成锁序反转。

        参数:
            run_id: 运行 id。

        返回:
            所属会话运行时；找不到时返回 None。
        """
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            active = runtime.active_run
            if active is not None and active.run_id == run_id:
                return runtime
        # 已结束的运行仍保留在运行时的 finished 集合中，仅用于状态码区分
        for runtime in runtimes:
            if run_id in runtime.finished_run_ids:
                return runtime
        return None

    def find_by_approval_id(self, approval_id: str) -> SessionRuntime | None:
        """按审批 id 查找所属会话运行时。

        优先匹配待决策审批；已决策或已过期的审批仍保留在审批器内部记录中，
        同样返回所属运行时，使审批接口能够区分「不存在」（404）与
        「已过期或已决策」（409）。查找在注册表锁内取得快照后进行。

        参数:
            approval_id: 审批 id。

        返回:
            所属会话运行时；找不到时返回 None。
        """
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            if approval_id in runtime.approval.pending_ids:
                return runtime
        for runtime in runtimes:
            if approval_id in runtime.approval.known_approval_ids:
                return runtime
        return None

    def release(self, session_id: str) -> None:
        """取消进行中的运行并释放会话运行时。

        参数:
            session_id: 会话 id。
        """
        with self._lock:
            runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            return
        active = runtime.active_run
        if active is not None and not active.cancel_token.is_cancelled():
            try:
                runtime.cancel_run(active.run_id)
            except (RunNotFoundError, RunFinishedError):
                logger.debug(
                    "释放运行时时运行已结束",
                    extra={"event": "runtime_release_run_finished", "session_id": session_id},
                )
        logger.info(
            "会话运行时已释放",
            extra={"event": "session_runtime_released", "session_id": session_id},
        )

    def delete_session(self, session_id: str) -> None:
        """取消运行、释放运行时并删除会话记录。

        参数:
            session_id: 会话 id。

        异常:
            SessionNotFoundError: 会话记录不存在。
        """
        if self._store.get_session(session_id) is None:
            raise SessionNotFoundError(f"会话 {session_id} 不存在")
        self.release(session_id)
        self._store.delete_session(session_id)
        logger.info(
            "会话已删除",
            extra={"event": "session_deleted", "session_id": session_id},
        )

    def close_all(self) -> None:
        """释放全部会话运行时（服务关闭时调用）。"""
        with self._lock:
            session_ids = list(self._runtimes)
        for session_id in session_ids:
            self.release(session_id)
