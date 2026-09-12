# Tasks

- [x] Task 1: 新增权限策略内核 `src/sagent/permissions/policy.py`
  - [x] SubTask 1.1: 定义 `Decision` 枚举（allow / ask / deny）
  - [x] SubTask 1.2: 定义 `PermissionRule` 数据类（tool、pattern（可选参数字符串模式）、decision）与 `PermissionRequest`（tool、args）
  - [x] SubTask 1.3: 定义 `DANGEROUS_SHELL_PATTERNS` 列表，**跨平台覆盖 Linux shell 与 Windows PowerShell**（Linux：`rm -rf /`、`mkfs`/`format`、`git reset --hard`、`git push -f`、`curl...\|(sh|bash)`、`DROP TABLE`/`DELETE FROM`、`:(){:\|:&};:`、`chmod -R 777 /`、`dd`/`shred`、`eval`、`/etc/passwd` 等；Windows/PowerShell：`del /s /q`、`rd /s /q`、`Remove-Item -Recurse -Force`、`Format-Volume`/`Clear-Disk`/`diskpart`、`shutdown /s`/`Stop-Computer`、`iex`/`Invoke-Expression`、`powershell -EncodedCommand`、`CertUtil -urlcache`、`reg add ... /f`、`net user ... /add`、`net localgroup administrators ... /add`、`Add-MpPreference -DisableRealtimeMonitoring`、`taskkill /f /im`、`vssadmin delete shadows` 等；全部忽略大小写）
  - [x] SubTask 1.4: 实现 `PermissionPolicy.decide(request) -> Decision`：先匹配用户规则（last-match-wins），未命中回退内置默认（read_file/memory/mcp_* -> allow，write_file/run_shell/未知 -> ask，run_shell 命中危险模式 -> deny）
  - [x] SubTask 1.5: 实现 `build_default_policy() -> PermissionPolicy`，提供上述内置默认规则
  - [x] SubTask 1.6: 模块顶部中文 docstring，`from __future__ import annotations`

- [x] Task 2: 新增交互审批器 `src/sagent/permissions/approval.py`
  - [x] SubTask 2.1: 定义 `ConfirmAction`（approved / denied / allowed_always / timed_out）
  - [x] SubTask 2.2: 定义 `ApprovalHandler` 抽象基类（`approve(request, policy) -> ConfirmAction`），并实现 `InteractiveApprovalHandler`：读 `stdin`，`y`=approved、`n`=denied、`a`=allowed_always；解析参数摘要与内置风险提示
  - [x] SubTask 2.3: 会话级 always-allow 白名单（`_always_allow` 集合），命中后直接返回 `allowed_always`（不再询问）
  - [x] SubTask 2.4: 超时/EOF/非交互处理：`input` 超时、`KeyboardInterrupt`/`EOFError`、无 TTY 时默认返回 denied（fail-safe），超时秒数与 non_interactive 动作由构造参数控制
  - [x] SubTask 2.5: 模块顶部中文 docstring，`from __future__ import annotations`

- [x] Task 3: 新增权限执行器 `src/sagent/permissions/enforcer.py`
  - [x] SubTask 3.1: 定义 `PermissionOutcome`（allowed: bool、message: str、decision: Decision）
  - [x] SubTask 3.2: 实现 `PermissionEnforcer.__init__(self, policy, approval, timeout=None, non_interactive="deny")`
  - [x] SubTask 3.3: 实现 `authorize(name, args) -> PermissionOutcome`：调用 `policy.decide` → allow 直接放行；deny 拒绝并返回 `message="错误: 工具 '<name>' 未获用户批准，已拒绝执行"`；ask 委托 `approval.approve`，据结果决定放行/拒绝
  - [x] SubTask 3.4: 审计日志：`event="permission_decision"`，含 tool、tool_args、decision、reason；拒绝/超时记 WARNING，放行记 INFO
  - [x] SubTask 3.5: 模块顶部中文 docstring，`from __future__ import annotations`

- [x] Task 4: `ToolRegistry` 单点拦截（`tools/registry.py`）
  - [x] SubTask 4.1: `__init__` 增加可选 `permission=None` 参数并保存为 `self._permission`
  - [x] SubTask 4.2: `execute()` 在参数校验通过后、`tool.run()` 前调用 `self._permission.authorize(name, parsed)`；被拒则直接返回拒绝 message；`permission is None` 时跳过授权（保持现有行为）
  - [x] SubTask 4.3: 返回的拒绝 message 为以"错误: 工具 '<name>' 未获用户批准，已拒绝执行"开头的字符串，供引擎作为 tool 观察回写

- [x] Task 5: 新增配置模型与示例（`config/models.py`、`config.example.yaml`、`config.yaml`）
  - [x] SubTask 5.1: `config/models.py` 新增 `PermissionConfig`（enabled、ask_timeout（缺省 600 秒，对齐业界询问等待尺度）、non_interactive、allow、deny、ask 规则列表），并在 `AppConfig` 挂接 `permissions` 字段（缺省启用）
  - [x] SubTask 5.2: `config.example.yaml` 新增 `permissions` 段（含注释说明规则格式、覆盖语义、审批超时缺省 600 秒、非交互默认 deny）
  - [x] SubTask 5.3: `config.yaml` 同步新增 `permissions` 段（沿用现有值）

- [x] Task 6: CLI 组装层默认启用权限（`cli/app.py` + `tools/__init__.py`）
  - [x] SubTask 6.1: `tools/__init__.py` 的 `build_default_registry(permission=None)` 增加可选透传参数
  - [x] SubTask 6.2: 当 `config.permissions.enabled` 时构建 `PermissionEnforcer(build_default_policy(), InteractiveApprovalHandler(...))`
  - [x] SubTask 6.3: 将该 enforcer 注入 `registry`（通过 `build_default_registry(permission=...)` 或 `registry` 参数）
  - [x] SubTask 6.4: 启动横幅打印权限状态（使用中/已关闭、ask 通知）
  - [x] SubTask 6.5: 工具执行链路 `core/react_engine.py` / `core/plan_engine.py` **不改动**（拒绝信息经 `execute()` 返回天然回写观察）

- [x] Task 7: 新增单元测试
  - [x] SubTask 7.1: `tests/unit/test_permissions.py`：policy 决策（内置默认、用户规则覆盖、危险模式 deny、last-match-wins）、approval 交互（y/n/a、always-allow 记忆、超时/EOF/非交互拒绝）
  - [x] SubTask 7.2: `test_permissions.py`：enforcer `authorize` 三态流（allow 放行、deny 拒绝 message、ask 经 approval 批准/拒绝）、审计日志事件 `permission_decision`
  - [x] SubTask 7.3: `tests/unit/test_tools.py` 补充：`ToolRegistry(permission=...)` 注入后的拦截/放行用例；`permission=None` 保持现有行为用例
  - [x] SubTask 7.4: `tests/unit/test_cli.py` 补充：CLI 组装层在 `permissions.enabled` 时注入 enforcer 的用例

- [x] Task 8: 运行全部测试确认通过
  - [x] SubTask 8.1: 执行 `python -m pytest`，确认全部用例通过且无回归

# Task Dependencies

- Task 1 是 Task 2/3/4/6 的依赖（需要 `Decision`、`PermissionPolicy`、`build_default_policy` 类型）
- Task 3 依赖 Task 1、Task 2（需要 policy 与 approval）
- Task 4 依赖 Task 3（透传 `PermissionEnforcer` 类型）
- Task 5 依赖 Task 1（规则格式对齐）
- Task 6 依赖 Task 3、Task 5
- Task 7 依赖 Task 1-6（测试需先有实现）
- Task 8 依赖 Task 7
