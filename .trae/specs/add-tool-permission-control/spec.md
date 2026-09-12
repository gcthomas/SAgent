# 工具调用权限控制 Spec

## 推荐方案概述

### 风险背景

SAgent 的工具调用链**没有任何权限控制**：CLI 交互循环（`cli/app.py`）→ 引擎（`core/react_engine.py`、`core/plan_engine.py`）→ `ToolRegistry.execute()`（`tools/registry.py`）→ `tool.run()`。LLM 通过 ReAct 循环自主调用工具，例如 `run_shell`（执行任意系统命令）、`write_file`（写入任意路径）、MCP 工具（外部服务），**全程无需用户批准**。一旦提示词被注入或 LLM 产生误判，可能执行 `rm -rf`、`format`、`git push -f`、覆盖用户文件、关闭系统防火墙/杀软等操作，造成不可逆损失。

当前唯一的过滤手段是 MCP 的 `tool_filter`（allow/deny，见 `tools/mcp/filtering.py`），其语义仅是"该工具是否暴露给 LLM（注册时过滤）"，**不涉及"执行时是否需用户批准"**，也不覆盖内置工具（`run_shell`/`write_file`/`read_file`）及 MCP 工具的执行期控制。

所有工具调用（无论内置、记忆还是 MCP）最终都经过 `ToolRegistry.execute()` 这一**唯一执行入口**，它是放置执行期权限闸门的天然单点。

### 业界最佳实践参考

结合 Claude Code、OpenAI Codex、OpenHands 等主流 Agent 的权限设计，核心共识：

- **三态决策模型**：`allow`（自动放行）/ `ask`（每次执行需用户批准）/ `deny`（直接拒绝）。仅二元 allow/block 不足以表达"危险操作需确认、常规操作免打扰"。
- **细粒度按参数匹配**：仅按工具名匹配太粗（同一 `run_shell` 既可 `ls` 也可 `rm -rf`）。业界对 `Bash(cmd:*)`、`Read(~/.ssh/**)` 等采用"工具 + 参数模式"联合匹配。SAgent 对应"工具名 + 参数字符串模式"。
- **Secure-by-default（默认安全）**：读操作自动放行；写/执行类默认需要确认；极度危险命令直接拒绝。开箱即安全，而非开箱即全自动。
- **单点执行闸门**：权限判定在工具执行的**唯一入口**统一进行，避免多点拦截造成的重复授权与不一致。
- **交互确认 + 会话级记忆**：`Y/N/A`——批准本次（y）、拒绝（n）、总是允许同一工具+参数（a，会话内记忆）。**超时/非交互/异常默认拒绝（fail-safe）**。
- **审批等待给足时间**：业界交互式 Agent 的权限确认普遍长时间等待用户而非短超时——Claude Code 的权限确认在交互模式下无限期挂起（`askUserQuestionTimeout` 缺省 `never`，可调档位 60s/5m/10m，最大档即 10 分钟），其命令超时上限 `BASH_MAX_TIMEOUT_MS` 默认即 600 秒；Gemini CLI 交互流程同样等待用户、不自动放行（Claude Code 曾短暂默认 60 秒自动继续，因社区反对两天内即回退）。SAgent 折中为**可配置的有限等待，缺省 600 秒（10 分钟）**：用户短暂离开（接水、切屏、短会）不会被误拒，无人值守会话也能按 fail-safe 收敛为拒绝而非永久挂起。
- **审计可观测**：每次决策记录结构化日志（决策、工具、参数、理由、命中规则），可追溯。

### SAgent 方案

贴合 SAgent「简单、通用、可扩展、零外部依赖、单测可跑」定位，方案为**独立的权限组件 + `ToolRegistry.execute()` 单点拦截 + config 可覆盖**：

- **新增 `src/sagent/permissions/`**（决策内核与交互层分离）：
  - `policy.py`：`Decision` 枚举（allow/ask/deny）、`PermissionRule`、`PermissionRequest`、`PermissionPolicy`（纯逻辑决策器，无 I/O、无 config 依赖，可独立单测）、`DANGEROUS_SHELL_PATTERNS`、`build_default_policy()`。
  - `approval.py`：`ConfirmAction`（approved/denied/allowed_always/timed_out）、`ApprovalHandler` 抽象接口 + `InteractiveApprovalHandler`（与用户交互、会话级 always-allow 记忆、超时/EOF/非交互拒绝）。
  - `enforcer.py`：`PermissionEnforcer`（统一入口：策略决策 + 审批确认 + 审计日志），是注入 `ToolRegistry` 的权限组件。
- **单点拦截（只改 `tools/registry.py`）**：`ToolRegistry.__init__` 新增可选 `permission: PermissionEnforcer | None`（缺省 `None` = 不启用，向后兼容）。`execute()` 在**参数校验通过后、`tool.run()` 之前**调用 `permission.authorize(name, parsed)`：`allow` 放行；`deny` 或 `ask` 被拒时**不执行**工具，直接返回以"错误: 工具 'xxx' 未获用户批准，已拒绝执行"开头的字符串（符合工具容错约定，引擎会把它作为 tool 观察回写给 LLM）。`permission is None` 时跳过授权，保持现有行为。
- **引擎零改动**：`react_engine.py`/`plan_engine.py` 不改——因为拒绝信息和正常工具结果一样走 `execute()` 返回值，引擎已天然将其作为观察消息处理。避免了双层拦截的重复授权、重复弹窗问题，也保证现有引擎测试完全不变。
- **CLI 层默认启用**：`cli/app.py` 构建引擎后，给 `registry` 注入 `PermissionEnforcer(build_default_policy(), InteractiveApprovalHandler(...))`（仅当 `config.permissions.enabled`），实现零配置即启用安全默认。
- **config 覆盖**：新增 `permissions` 配置段（enabled / ask_timeout / non_interactive / allow / deny / ask 规则列表），用于覆盖内置默认规则与超时/非交互行为。
- **审计**：每次决策在 enforcer 内记 `event="permission_decision"`（decision/tool/args/reason）；拒绝与超时记 WARNING，放行记 INFO。

### 内置默认规则（build_default_policy）

决策语义：**先匹配用户配置规则（permissions 段），未匹配则回退内置默认规则**。按"工具名 + 可选参数字符串模式"匹配，同一模式最后匹配者生效（last-match-wins）。用户显式配置的 `allow` 可覆盖内置 `deny`（视为用户的明确授权）。

| 工具/模式 | 默认决策 | 理由 |
|---|---|---|
| `read_file` | `allow` | 只读，成本低、风险小 |
| `add_memory` / `replace_memory` / `remove_memory` | `allow` | 记忆工具已由 `memory/security.py` 写入前安全扫描把关 |
| `mcp_*`（全部 MCP 工具） | `allow` | 已在注册时经 `tool_filter` 过滤，默认不额外打断；用户可在 config 覆盖 |
| `write_file` | `ask` | 写盘可能覆盖/新建用户文件，需确认 |
| `run_shell` | `ask` | 执行任意命令，默认一律需要用户批准（安全默认） |
| 其它未匹配工具 | `ask` | 未知工具保守处理，默认需确认 |
| `run_shell` + 命中 `DANGEROUS_SHELL_PATTERNS` | `deny` | 极高危险、几乎无合法用途，直接拒绝（可通过 config 放宽） |

#### DANGEROUS_SHELL_PATTERNS（跨平台：Linux shell 与 Windows PowerShell）

`run_shell` 在 Windows 上经由 PowerShell `-EncodedCommand` 执行，因此危险清单必须同时覆盖类 Unix shell 与 PowerShell。以下模式均忽略大小写，命中即 `deny`：

**通用 / 跨平台（Linux + Windows）**

- `git reset --hard`、`git push -f` / `git push --force`（破坏/强制推送）
- `DROP TABLE` / `DELETE FROM` / `TRUNCATE TABLE`（数据库破坏性语句）
- `chmod -R 777 /`、`chmod -R 777 系统根`、`chown -R`（全盘提权/改属主）
- `mv /`、`cp -r 数据到 /dev/null`（破坏性移动/覆盖）

**Linux / 类 Unix 特有**

- `rm -rf /`、`rm -rf ~`、`rm -rf .`（危险删除）
- `mkfs`、`format`、`dd if=/dev/zero` / `/dev/urandom`、`shred`（整盘擦除）
- `:(){ :|:& };:`（fork 炸弹）
- `curl ... | sh` / `wget ... | bash` / `base64 -d | sh`（下载后管道执行）
- `eval`、`kill -9 -1`、`pkill -9`、`useradd` / `usermod -o -u 0`、`passwd`（进程/账户高危操作）
- 引用 `/etc/passwd`、`/etc/shadow`、`/etc/sudoers`、`authorized_keys`、`crontab`、`/dev/tcp/`、`/dev/udp/`（敏感文件/网络管道）
- `sudo rm`、`sudo shutdown -h now`（提权后高危）

**Windows / PowerShell 特有**

- `del /s /q`、`rd /s /q`、`rmdir /s /q`、`Remove-Item -Recurse -Force`、`Remove-Item C:\ -Recurse -Force`（递归强制删除）
- `Format-Volume`、`Clear-Disk`、`Initialize-Disk`、`diskpart`、`format c:`、`cipher /w`（磁盘/分区清空）
- `Stop-Computer` / `Restart-Computer`、`shutdown /s` / `shutdown /r`（关机/重启）
- `iex`（`Invoke-Expression`，含 `iex (New-Object Net.WebClient).DownloadString(...)`）、`Invoke-Expression`（下载后远程执行）
- `Invoke-WebRequest ... -OutFile` 后跟 `Start-Process`（下载并启动）
- `powershell -EncodedCommand`、`powershell -c ... DownloadString`、`cmd /c ... 下载并执行`、`mshta`、`regsvr32`、`CertUtil -urlcache`、`bitsadmin`（下载执行/绕过）
- `reg add ... /f`（写注册表，如自启动/Run 键）、`reg delete`、`sc stop`/`sc delete`（服务破坏）
- `net user ... /add`、`net localgroup administrators ... /add`、`Add-MpPreference -DisableRealtimeMonitoring`、`Set-MpPreference -DisableRealtimeMonitoring`（创建提权账户/关闭 Defender）
- `taskkill /f /im`、`Stop-Process -Force`、`ntdsutil`、`vssadmin delete shadows`、`wmic process call create`（进程/凭证/影子备份破坏）

> 说明：内置危险清单只做**极端危险**（几乎无合法用途、一旦执行难以挽回）的命令拦截，遵循"绝不误伤正常开发操作、宁可漏网走 ask 也不误拒绝"的原则。无法穷举的场景由 `run_shell` 默认 `ask` 兜底，确保任何未列入但命令存在风险的操作仍需用户确认。用户可通过 config `deny`/`allow` 增删规则。

### 与替代方案对比（为何不选）

- **引擎层拦截（而非注册表）**：引擎直接调用 `registry.execute()`，若在引擎层另加授权，同一工具会被授权两次（引擎一次、注册表一次），ask 会重复弹窗、审计重复；且引擎测试多，改动面大。故仅在注册表这一**唯一执行入口**拦截。
- **在 `registry.execute` 内硬编码权限逻辑**：权限与执行强耦合，且破坏"工具纯执行"职责。故抽取独立 `PermissionEnforcer` 组件注入。
- **仅 config 声明、无内置默认**：开箱不安全，现有用户升级后仍然全自动，违背"危险工具默认 ask"。
- **仅按工具名匹配**：无法区分同一工具内的安全/危险参数（`run_shell` 的 `ls` 与 `rm -rf`）。
- **LLM-as-judge 判定危险**：每轮多一次 LLM 调用，成本高、不可复现、判定模型本身可注入；规则化优先。
- **非交互直接放行**：违背 fail-safe；非交互（无 TTY）或超时一律拒绝（默认 deny），除非配置显式改为 allow。

## Why

Agent 调用工具时无任何权限控制，LLM 可自主执行 `run_shell`（任意命令）、`write_file`（任意写入）及 MCP 工具，存在执行危险命令、覆盖用户文件的不可逆风险。需为工具调用增加**细粒度权限控制**：危险命令默认需用户批准（ask），只读操作自动放行（allow），极高危险命令直接拒绝（deny），支持 config 覆盖，并记录决策审计。

## What Changes

- 新增 `src/sagent/permissions/policy.py`：`Decision`（allow/ask/deny）、`PermissionRule`、`PermissionRequest`、`PermissionPolicy`（纯逻辑决策，last-match-wins）、`DANGEROUS_SHELL_PATTERNS`（跨平台）、`build_default_policy()`。
- 新增 `src/sagent/permissions/approval.py`：`ConfirmAction`（approved/denied/allowed_always/timed_out）、`ApprovalHandler` 抽象类、`InteractiveApprovalHandler`（交互 + 会话级 always-allow + 超时/EOF/非交互拒绝）。
- 新增 `src/sagent/permissions/enforcer.py`：`PermissionEnforcer`，`authorize(name, args) -> PermissionOutcome` 统一执行策略决策 + 审批确认 + 审计。
- 修改 `src/sagent/tools/registry.py`：`__init__` 新增可选 `permission=None`；`execute()` 在参数校验通过后、`tool.run()` 前调用 `permission.authorize`，被拒则直接返回拒绝 message；`permission is None` 时跳过（保持现有行为）。
- 修改 `src/sagent/config/models.py`：新增 `PermissionConfig`（enabled、ask_timeout（缺省 600 秒）、non_interactive、allow/deny/ask 规则列表）并挂到 `AppConfig.permissions`。
- 修改 `config.example.yaml` 与 `config.yaml`：新增 `permissions` 段（含注释说明规则格式、覆盖语义、非交互默认 deny）。
- 修改 `src/sagent/cli/app.py`：当 `config.permissions.enabled` 时构建 `PermissionEnforcer`，注入 `registry`；启动横幅打印权限状态。
- 修改 `src/sagent/tools/__init__.py`：`build_default_registry()` 增加可选 `permission` 透传参数（缺省 `None`）。
- **不修改** `core/react_engine.py`、`core/plan_engine.py`（引擎零改动，拒绝走 execute 返回值天然回写观察）、`tools/mcp/*`、`tools/shell_tool.py`、`tools/file_tools.py`、`tools/base.py`、`config/loader.py`、`context/`、`session/`、`memory/`、`observability/`。
- 新增单元测试 `tests/unit/test_permissions.py`（policy 决策、approval 交互、enforcer 授权/审计）、补充 `tests/unit/test_tools.py`（registry 注入 permission 的拦截/放行、permission=None 保持现有行为）、补充 `tests/unit/test_cli.py`（enabled 时注入 enforcer）。

## Impact

- **Affected specs**: `build-cli-react-agent`（在工具执行链路上叠加权限层，不改其 ReAct/Plan 循环、工具 schema 与容错约定）。
- **Affected code**:
  - 新增：`src/sagent/permissions/__init__.py`、`policy.py`、`approval.py`、`enforcer.py`
  - 修改：`tools/registry.py`、`config/models.py`、`config.example.yaml`、`config.yaml`、`cli/app.py`、`tools/__init__.py`
  - 不修改：`core/react_engine.py`、`core/plan_engine.py`、`tools/mcp/*`、`tools/shell_tool.py`、`tools/file_tools.py`、`tools/base.py`、`config/loader.py`、`context/`、`session/`、`memory/`、`observability/`。

## ADDED Requirements

### Requirement: 三态决策内核（allow/ask/deny）

系统 SHALL 提供独立于执行引擎的权限决策内核 `PermissionPolicy`，对"工具名 + 参数字符串"输出三态决策 `allow`（自动放行）/ `ask`（需用户批准）/ `deny`（直接拒绝）。决策匹配按"用户配置规则优先、内置默认规则兜底"，同一模式最后匹配者生效（last-match-wins）。嵌套内核为纯逻辑、无 I/O、无 config 依赖，可独立单测。

#### Scenario: 只读工具自动放行

- **WHEN** LLM 调用 `read_file`（未命中任何用户规则）
- **THEN** 决策为 `allow`，工具直接执行，无需用户干预

#### Scenario: 危险工具需要批准

- **WHEN** LLM 调用 `run_shell` 或 `write_file`（未命中任何用户规则）
- **THEN** 决策为 `ask`，进入审批流程

#### Scenario: 极度危险命令直接拒绝

- **WHEN** `run_shell` 的参数命中 `DANGEROUS_SHELL_PATTERNS`（如包含 `rm -rf /`、`git reset --hard`、`curl|sh`、`del /s /q`、`Format-Volume`、`iex`、`net user ... /add`）
- **THEN** 决策为 `deny`，不进入审批流程，直接返回被拒

#### Scenario: 用户规则覆盖内置默认

- **WHEN** 用户在 config `permissions` 段声明某工具/模式为 `allow`
- **THEN** 该匹配命中用户规则，决策为 `allow`（作为用户明确授权，可覆盖内置 `deny`），不再触发内置默认的 `ask`/`deny`

### Requirement: 交互审批器（Y/N/A + 超时拒绝）

系统 SHALL 提供交互审批器 `InteractiveApprovalHandler`，当决策为 `ask` 时向用户请求确认：输入 `y` 批准本次执行，`n` 拒绝，`a` 总是允许（在**本次会话内**记录该"工具+参数"的 always-allow 白名单，后续同模式自动放行）。在无交互终端（TTY 不可用）、输入超时（超过 `ask_timeout` 秒，**缺省 600 秒 / 10 分钟**）或 EOF/中断时，SHALL 默认**拒绝**（fail-safe）。缺省 600 秒对齐业界询问等待尺度（Claude Code 权限确认可无限期等待、其询问自动继续档位最大 10m、`BASH_MAX_TIMEOUT_MS` 默认 600s），确保用户短暂离开（接水、切屏、短会）不被误拒。审批器 SHALL 显示工具名、参数摘要与内置规则的风险提示。

#### Scenario: 批准一次

- **WHEN** 用户输入 `y`
- **THEN** 工具执行，本次放行，不写入 always-allow 白名单

#### Scenario: 总是允许（会话内记忆）

- **WHEN** 用户输入 `a`
- **THEN** 工具执行，并将该"工具+参数模式"记入本会话 always-allow 集合；后续相同模式自动放行，不再询问
- **AND** 该记忆仅限当前会话，会话结束即失效

#### Scenario: 拒绝执行

- **WHEN** 用户输入 `n`
- **THEN** 工具不执行，返回被拒反馈

#### Scenario: 超时默认拒绝（fail-safe）

- **WHEN** 审批等待超过 `ask_timeout` 秒，或发生 EOF/中断，或检测到非交互终端
- **THEN** 默认拒绝执行（除非 config `non_interactive` 显式设为 `allow`）

### Requirement: 单点执行拦截（ToolRegistry）

系统 SHALL 在 `ToolRegistry.execute()` 参数校验通过后、`tool.run()` 执行前，调用注入的权限组件授权。当决策为 `deny` 或 `ask` 被拒绝时，SHALL 不执行工具，而把以"错误:"开头的拒绝说明作为 `execute()` 的返回值。引擎（ReAct/Plan）SHALL 无需修改——拒绝信息与正常结果同为字符串返回值，天然作为 tool 观察回写给 LLM。`permission is None` 时 SHALL 跳过授权，保持现有行为（向后兼容）。

#### Scenario: 允许后正常执行

- **WHEN** 决策为 `allow` 或用户批准 `ask`
- **THEN** 正常执行工具并返回真实观察结果

#### Scenario: 被拒绝返回错误观察

- **WHEN** 决策为 `deny` 或用户拒绝 `ask`
- **THEN** 不执行命令，`execute()` 返回"错误: 工具 '<name>' 未获用户批准，已拒绝执行"
- **AND** 引擎将上述字符串作为 tool 观察写入消息历史，Agent 主流程继续运行，不中断、不抛异常

#### Scenario: 未注入权限保持全自动

- **WHEN** `ToolRegistry()` 未注入 `permission`（缺省 `None`）
- **THEN** 工具全自动执行，无任何拦截，行为与旧版一致

### Requirement: 配置可覆盖

系统 SHALL 通过新增 `permissions` 配置段支持覆盖：`enabled`（是否启用，缺省 `true`）、`ask_timeout`（审批超时秒数，缺省 `600`）、`non_interactive`（无交互/超时时的动作，缺省 `deny`，可显式 `allow`）、以及 `allow` / `deny` / `ask` 三个工具/模式规则列表，用于覆盖内置默认规则。新增配置需同步更新 `config/models.py`、`config.example.yaml`、`config.yaml` 与加载逻辑（复用现有 `load_config` 的 pydantic 校验，不新增解析路径）。

#### Scenario: 关闭权限控制恢复旧行为

- **WHEN** 用户将 `permissions.enabled` 设为 `false`
- **THEN** 引擎与注册表不注入权限组件，工具回到全自动执行（与旧行为一致），启动横幅提示权限已关闭

#### Scenario: 用户将危险命令加入 allow

- **WHEN** 用户在 `permissions` 的 `allow` 列表加入 `run_shell`（或 `run_shell: 具体命令模式`）
- **THEN** 该工具/模式决策为 `allow`，不再触发默认的 `ask`/`deny`（作为用户明确授权）

### Requirement: 决策审计可观测

系统 SHALL 在每次权限决策时输出结构化日志：`event="permission_decision"`，含 `decision`（allow/ask/deny）、`tool`、`tool_args`、`reason`（命中规则/拒绝理由）。拒绝与超时记 WARNING，放行记 INFO。

#### Scenario: 决策审计日志

- **WHEN** 引擎执行一次工具并触发权限决策
- **THEN** 记录 `event="permission_decision"` 日志，含工具名、参数与决策结果与理由

### Requirement: 向后兼容（现有测试不破坏）

系统 SHALL 保持现有构造兼容：`ToolRegistry` 的 `permission` 参数可选、缺省 `None`；现有 `ToolRegistry()`、`build_default_registry()` 及引擎测试在不传入权限组件时行为不变（不启用权限拦截）。权限的默认启用发生在 CLI 组装层（`cli/app.py`），而非注册表/引擎内部默认，从而不改变引擎单元测试与工具测试的执行路径。

#### Scenario: 注册表不传权限保持自动执行

- **WHEN** 现有测试以 `build_default_registry()` 构造并触发 `read_file`
- **THEN** 工具正常自动执行，无权限拦截，测试行为不变
