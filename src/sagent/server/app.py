"""Web 服务应用工厂。

按 CLI 的依赖装配顺序构建 Web 服务所需的共享依赖：LLM 客户端、权限执行器、
工具注册表、长期记忆、Skill 与 MCP，并完成 CORS、健康检查、统一错误响应与
前端静态资源托管。ContextManager / SessionManager / 执行引擎是会话级对象，
由运行时注册表按会话惰性构建，本模块不创建其单例。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..cli.app import _build_permission
from ..config.models import AppConfig
from ..context.token_counter import count_text_tokens
from ..llm.client import LLMClient
from ..memory import MemoryManager, MemoryStore
from ..observability import get_logger
from ..permissions import PermissionEnforcer
from ..session.store import SessionStore
from ..skills import SkillLoader, SkillManager
from ..tools import AddMemoryTool, RemoveMemoryTool, ReplaceMemoryTool, build_default_registry
from ..tools.mcp import MCPSessionManager, build_mcp_providers
from .runtime import RuntimeRegistry
from .routers import register_routers

logger = get_logger(__name__)

# 前端未构建时的根路径提示
_UNBUILT_FRONTEND_MESSAGE = "前端未构建，请先在 web/ 目录执行 npm install && npm run build"

# HTTP 状态码到统一错误码的映射
_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    500: "internal_error",
}


def _error_code(status_code: int) -> str:
    """返回状态码对应的统一错误码。

    参数:
        status_code: HTTP 状态码。

    返回:
        简洁的英文错误码。
    """
    return _ERROR_CODES.get(status_code, "error")


def _resolve_static_dir(config: AppConfig) -> Path:
    """解析前端静态资源目录，相对路径按运行目录展开为绝对路径。

    参数:
        config: 应用总配置。

    返回:
        静态资源目录的绝对路径。
    """
    static_dir = Path(config.server.static_dir)
    if not static_dir.is_absolute():
        static_dir = Path.cwd() / static_dir
    return static_dir


def create_app(config: AppConfig) -> FastAPI:
    """构建并返回 Web 服务应用实例。

    参数:
        config: 应用总配置。

    返回:
        已完成依赖装配与路由注册的 FastAPI 应用。
    """
    # 共享依赖按 CLI 的装配顺序构建
    llm = LLMClient(config.llm)
    permission: PermissionEnforcer | None = _build_permission(config.permissions)
    registry = build_default_registry(permission=permission)

    # 长期记忆管理器（仅当 memory 启用时），并向工具表追加记忆工具
    memory_manager: MemoryManager | None = None
    if config.memory.enabled:
        memory_store = MemoryStore(
            directory=Path.cwd() / config.memory.dir,
            user_max_chars=config.memory.user_max_chars,
            memory_max_chars=config.memory.memory_max_chars,
        )
        memory_manager = MemoryManager(memory_store, llm)
        registry.register(AddMemoryTool(memory_manager))
        registry.register(ReplaceMemoryTool(memory_manager))
        registry.register(RemoveMemoryTool(memory_manager))

    # Skill 管理器与正文加载器（仅当 skills 启用时），启动时执行一次发现扫描
    skill_manager: SkillManager | None = None
    skill_loader: SkillLoader | None = None
    if config.skills.enabled:
        from ..tools.skill_tool import LoadSkillTool, ReadSkillResourceTool

        skill_manager = SkillManager(config.skills)
        skill_manager.discover()
        skill_loader = SkillLoader(
            skill_manager,
            max_body_tokens=config.skills.max_body_tokens,
            max_output_tokens=config.context.max_tool_output_tokens,
            max_resource_tokens=config.skills.max_resource_tokens,
            token_counter=count_text_tokens,
            token_counter_kwargs={
                "model": config.context.summary_llm.model if config.context.summary_llm is not None else "",
                "method": config.context.token_counter_method,
            },
        )
        registry.register(LoadSkillTool(skill_loader))
        registry.register(ReadSkillResourceTool(skill_loader))

    # MCP 工具提供者（仅当 mcp 启用时），启动会话管理器并注册 MCP 工具
    mcp_session_manager: MCPSessionManager | None = None
    if config.mcp.enabled:
        mcp_session_manager = MCPSessionManager()
        mcp_session_manager.start()
        for provider in build_mcp_providers(config.mcp, mcp_session_manager):
            registry.register_provider(provider)

    # 会话存储（仅当 session 启用时）；SessionManager 由会话级运行时创建
    session_store: SessionStore | None = None
    if config.session.enabled:
        session_store = SessionStore(config.session.db_path, config.session.enable_fts)

    # 会话级运行时注册表（仅在会话管理启用时可用）
    runtimes: RuntimeRegistry | None = None
    if session_store is not None:
        runtimes = RuntimeRegistry(
            config=config,
            store=session_store,
            llm=llm,
            memory_manager=memory_manager,
            skill_loader=skill_loader,
            mcp_session_manager=mcp_session_manager,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Web 服务生命周期：关闭时释放运行时并关闭 MCP 连接。"""
        logger.info("Web 服务启动", extra={"event": "server_startup"})
        yield
        if runtimes is not None:
            runtimes.close_all()
        if mcp_session_manager is not None:
            mcp_session_manager.shutdown()
        logger.info("Web 服务关闭", extra={"event": "server_shutdown"})

    app = FastAPI(title="SAgent Web", version=__version__, lifespan=lifespan)

    # 共享依赖统一挂在 app.state，供路由与会话级运行时复用
    app.state.config = config
    app.state.store = session_store
    app.state.llm = llm
    app.state.tool_registry = registry
    app.state.agent_config = config.agent
    app.state.planning_config = config.planning
    app.state.permissions_config = config.permissions
    app.state.permission_enforcer = permission
    app.state.memory = memory_manager
    app.state.skill_manager = skill_manager
    app.state.skill_loader = skill_loader
    app.state.mcp_session_manager = mcp_session_manager
    app.state.runtimes = runtimes
    app.state.static_dir = _resolve_static_dir(config)

    # CORS：默认仅允许本机开发来源
    if config.server.enable_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.server.cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """将 HTTPException 统一为 {code, message, detail} 结构。"""
        detail: Any = exc.detail
        message = detail if isinstance(detail, str) else "请求处理失败"
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": _error_code(exc.status_code), "message": message, "detail": detail},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """将请求参数校验错误统一为 {code, message, detail} 结构。"""
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "请求参数校验失败",
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """健康检查：返回服务状态与版本。"""
        return {"status": "ok", "version": __version__}

    # 业务 API 路由（会话与轨迹），须在静态挂载与 SPA 回退之前注册
    register_routers(app)

    # 前端静态资源托管：仅挂载存在的 assets 目录
    static_dir = app.state.static_dir
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Any:
        """SPA 回退：非 /api 路径返回静态文件或 index.html。

        静态目录不存在时返回明确的中文未构建提示；未知 /api 路径保持 404，
        不返回 index.html，避免前端页面掩盖接口错误。
        """
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        if not static_dir.is_dir():
            return HTMLResponse(_UNBUILT_FRONTEND_MESSAGE)
        if full_path:
            candidate = (static_dir / full_path).resolve()
            if candidate.is_file() and static_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
        index_file = static_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse(_UNBUILT_FRONTEND_MESSAGE)

    return app
