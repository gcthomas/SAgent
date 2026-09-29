# `/sessions` 会话列表分页方案

## Summary

为 `/sessions` 增加默认限量和分页能力，避免会话数量增长后一次性输出全部记录造成终端刷屏。命令保持当前斜杠命令解析方式不变，采用以下语法：

- `/sessions`：显示第 1 页，默认每页 20 条。
- `/sessions <每页数量>`：按指定每页数量显示第 1 页，例如 `/sessions 50`。
- `/sessions <每页数量> <页码>`：显示指定页，例如 `/sessions 20 2`。

查询限制下沉到 SQLite 的 `LIMIT/OFFSET`，同时保留最近更新时间优先的列表行为。结果使用 `updated_at DESC, id DESC` 稳定排序，避免分页过程中同一更新时间的会话顺序不稳定。

## Current State Analysis

- [src/sagent/cli/app.py](../../src/sagent/cli/app.py) 的 `_dispatch_session_command` 在 `sessions` 分支调用 `session_manager.list_sessions()`，无参数获取所有会话并逐条打印。
- [src/sagent/session/manager.py](../../src/sagent/session/manager.py) 的 `list_sessions()` 无参数，仅透传存储层。
- [src/sagent/session/store.py](../../src/sagent/session/store.py) 的 `SessionStore.list_sessions()` 使用 `ORDER BY updated_at DESC` 后 `fetchall()`，因此既没有数据库级数量限制，也没有分页偏移。
- [src/sagent/cli/commands.py](../../src/sagent/cli/commands.py) 已将斜杠命令拆分为 `ParsedCommand(name, args)`；`/sessions 20 2` 无需修改通用解析器即可得到两个参数。
- [tests/unit/test_session_store.py](../../tests/unit/test_session_store.py) 目前仅覆盖完整列表的更新时间倒序，没有覆盖 limit、offset 或相同更新时间。
- [tests/unit/test_cli.py](../../tests/unit/test_cli.py) 目前只覆盖通用命令解析，没有覆盖 `_dispatch_session_command` 的会话列表输出和参数错误提示。

业界 Agent CLI 的常见做法是默认进入近期会话列表，并通过交互选择器、搜索或扩大范围来浏览历史，而不是无条件输出全部记录。Claude Code 的 `/resume` 使用可导航、可搜索的会话选择器；Codex CLI 提供最近会话快捷入口和完整选择器。结合当前项目已有的纯文本斜杠命令架构，本次选择低侵入的限量分页，不引入新的 TUI/交互选择器。

参考：

- [Claude Code session picker](https://code.claude.com/docs/en/sessions#use-the-session-picker)
- [Claude Code session management](https://code.claude.com/docs/en/sessions#resume-a-session)
- [Codex CLI session workflow article](https://codex.danielvaughan.com/2026/05/08/codex-cli-v0129-session-workflow-upgrades-resume-picker-scrollback-diff/)

## Proposed Changes

### 1. 扩展存储层列表查询

文件：[src/sagent/session/store.py](../../src/sagent/session/store.py)

- 将 `SessionStore.list_sessions()` 扩展为支持 `limit: int | None = None` 和 `offset: int = 0`，保持无参数调用时仍可返回全部记录，避免影响其他现有调用方。
- SQL 继续选择现有全部元数据字段，增加稳定排序 `ORDER BY updated_at DESC, id DESC`。
- 当传入 `limit` 时使用 SQLite `LIMIT ? OFFSET ?`；不传 `limit` 时保留现有全量查询行为。
- 不在存储层隐式设置默认 20，默认值属于 `/sessions` CLI 展示策略；这样存储 API 仍能支持完整列表和其他调用场景。

### 2. 扩展会话管理器接口

文件：[src/sagent/session/manager.py](../../src/sagent/session/manager.py)

- 将 `SessionManager.list_sessions()` 扩展为接收同样的 `limit` 和 `offset` 参数，并透传到 `SessionStore.list_sessions()`。
- 更新函数中文说明，明确排序、数量和偏移语义。
- 保留现有无参数调用的行为，避免修改与本需求无关的会话管理逻辑。

### 3. 增加 `/sessions` 参数解析、分页输出和帮助文本

文件：[src/sagent/cli/app.py](../../src/sagent/cli/app.py)

- 在 `sessions` 分支使用常量默认每页 20 条；解析 0、1 或 2 个位置参数，分别映射为 `limit` 和 `page`。
- 将页码转换为 `offset = (page - 1) * limit`，调用存储层分页查询。
- 为了判断是否存在下一页，查询 `limit + 1` 条，实际只打印前 `limit` 条；如果多出一条，打印简洁的下一页提示，例如 `还有更多会话，可使用 /sessions 20 2 查看下一页`。当页码大于 1 时打印上一页提示；不引入额外总数查询。
- 保持现有当前会话 `*` 标记、空列表提示和会话行格式。
- 对参数数量超过 2、非整数、`limit <= 0` 或 `page <= 0` 输出用法提示并提前返回，不调用会话管理器。用法文本明确 `/sessions [每页数量] [页码]` 和默认值 20。
- 更新会话帮助中的 `/sessions` 描述，不再写“列出全部会话”，改为“分页列出会话”。

### 4. 补充存储层测试

文件：[tests/unit/test_session_store.py](../../tests/unit/test_session_store.py)

新增测试覆盖：

- 指定 `limit` 只返回最近指定数量的会话。
- 指定 `limit` 和 `offset` 返回正确页。
- `updated_at` 相同的会话按 `id DESC` 稳定排序。
- 保留并继续通过现有无参数全量列表测试，验证兼容原调用方式。

### 5. 补充 CLI 分发测试

文件：[tests/unit/test_cli.py](../../tests/unit/test_cli.py)

- 为 `_dispatch_session_command` 构造轻量 fake session manager，使用 `capsys` 验证 `/sessions` 默认传入 `limit=21, offset=0`，但只打印 20 条并显示下一页提示。
- 验证 `/sessions 5 2` 传入 `limit=6, offset=5`，打印对应页并显示上一页/下一页提示的正确状态。
- 验证无效参数和参数过多时输出用法，且不执行列表查询。
- 验证空结果仍输出“暂无会话”。
- 保持通用 `parse_command` 测试不变，因为解析器无需修改。

## Assumptions & Decisions

- 默认页大小固定为 20，不新增配置项；用户需求是控制刷屏，CLI 默认值足以解决问题，配置扩展会增加不必要的维护面。
- 分页参数采用位置参数而不是 `--limit`/`--page`，因为当前 `ParsedCommand` 只提供简单空白分割，且 `/switch`、`/rename` 等命令已经采用位置参数约定。
- 第一个参数表示每页数量，第二个参数表示从 1 开始的页码；不支持只指定页码，避免命令含义歧义。
- 只读列表不需要总数查询；通过多取一条记录判断是否有下一页，避免为每次展示增加额外 `COUNT(*)` 查询。
- 稳定排序使用现有更新时间优先并以会话 ID 作为并列时的确定性次序，不改变主要用户感知排序。
- 本次不实现交互式选择器、模糊搜索、归档或自动清理，这些属于后续会话生命周期功能，不是当前刷屏问题的必要范围。

## Verification

1. 运行受影响测试：
   `python -m pytest tests/unit/test_session_store.py tests/unit/test_cli.py`
2. 运行完整离线测试：
   `python -m pytest`
3. 手工验证以下交互行为：
   - `/sessions` 默认最多输出 20 条。
   - `/sessions 5` 输出第 1 页并在有更多记录时提示第 2 页。
   - `/sessions 5 2` 输出第 2 页，并正确提示上一页/下一页。
   - `/sessions 0`、`/sessions abc`、`/sessions 5 0` 和超过两个参数均输出用法提示。
4. 检查新增中文文本编码无乱码，并确认未修改与会话列表无关的功能。
