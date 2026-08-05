# Tasks

- [x] Task 1: 新增安全扫描器 `src/sagent/memory/security.py`
  - [x] SubTask 1.1: 定义 `ScanResult` 数据结构（`sanitized: str` / `blocked: bool` / `reasons: list[str]` / `categories: list[str]`）
  - [x] SubTask 1.2: 实现 `MemorySecurityScanner.__init__(self)`，无参数，四类规则全部启用、不可关闭
  - [x] SubTask 1.3: 实现不可见字符净化（C0/C1 控制字符除 `\t\n\r`、零宽字符 U+200B/200C/200D/2060/FEFF、双向控制符 U+202A-202E/U+2066-2069），返回净化后文本与移除字符数
  - [x] SubTask 1.4: 实现凭证检测（PEM 私钥、通用凭证赋值、OpenAI/AWS/GitHub/Slack/Google 平台前缀、Bearer token）
  - [x] SubTask 1.5: 实现 Shell 威胁检测（authorized_keys、/etc/passwd 等、/dev/tcp、rm -rf /、curl|sh 类、base64 -d | sh、eval 等，忽略大小写）
  - [x] SubTask 1.6: 实现 Prompt 注入检测（中英文组合模式，降低误报：英文 ignore/disregard/forget/override/you are now/system:/act as；中文 忽略+指令/提示/规则、你现在是、新指令、系统提示、覆盖之前的、假装你是、扮演）
  - [x] SubTask 1.7: 实现 `scan(content)` 主流程：先净化→ 依次执行三类拒绝检测→ 任一命中则 `blocked=True` 并收集 reasons/categories；返回 `ScanResult`
  - [x] SubTask 1.8: 模块顶部中文 docstring，`from __future__ import annotations`，独立 logger

- [x] Task 2: `MemoryManager` 集成安全扫描（默认强制启用）
  - [x] SubTask 2.1: `__init__` 增加可选 `scanner` 参数（缺省 `None`，仅供测试注入）；`scanner is None` 时内部 `self._scanner = MemorySecurityScanner()` 强制创建默认扫描器，绝不落入不扫描状态
  - [x] SubTask 2.2: 新增私有 `_scan(target, content) -> tuple[bool, str]`：调用 `self._scanner.scan(content)`；命中拒绝返回 `(False, "错误:记忆内容未通过安全扫描：<reasons>，已拒绝写入")` 并记 `memory_security_block` 审计日志；通过返回 `(True, sanitized)`，发生净化时记 `memory_security_sanitized` 日志
  - [x] SubTask 2.3: `add` 在 `self._store.append` 前调用 `_scan`，拦截则返回错误字符串不写入，通过则用净化后内容写入（保留原"已添加"返回与 `_maybe_reflect` 调用）
  - [x] SubTask 2.4: `replace` 对 `new` 调用 `_scan`（`old` 不扫描，用于精确匹配），拦截返回错误字符串，通过则用净化后 new 写入（保留原 found 判断与未找到提示）
  - [x] SubTask 2.5: `_reflect` 在 `self._store.write_all` 前对整理后内容调用 `_scan`，拦截则记日志并保留写入前内容、不抛异常；通过则用净化后内容写回
  - [x] SubTask 2.6: `remove` 不扫描（保留原逻辑不变）

- [x] Task 3: 新增单元测试 `tests/unit/test_memory_security.py`
  - [x] SubTask 3.1: 不可见字符净化命中（零宽字符、控制字符）与正常空白保留用例
  - [x] SubTask 3.2: 凭证检测命中（私钥、API key、Bearer）与正常技术名词不误报用例
  - [x] SubTask 3.3: Shell 威胁检测命中（authorized_keys、curl|sh、rm -rf /）与正常命令不误报用例
  - [x] SubTask 3.4: Prompt 注入检测命中（中英文）与"忽略琐碎信息"不误报用例
  - [x] SubTask 3.5: `MemoryManager` 集成用例：生产构造（不传 scanner）自动启用扫描；拦截不落盘（断言 MemoryStore 文件不变）；净化后落盘；`_reflect` 整理后内容被拦截保留旧内容；注入 fake scanner 覆盖默认
  - [x] SubTask 3.6: 审计日志事件用例（`memory_security_block` / `memory_security_sanitized`）

- [x] Task 4: 运行全部测试确认通过
  - [x] SubTask 4.1: 执行 `python -m pytest`，确认全部用例通过且无回归

# Task Dependencies

- Task 2 依赖 Task 1（需要 `MemorySecurityScanner` 类型）
- Task 3 依赖 Task 1、Task 2
- Task 4 依赖 Task 3（测试需先编写）及前置所有实现任务
- Task 1 内部子任务 1.7 依赖 1.3-1.6
