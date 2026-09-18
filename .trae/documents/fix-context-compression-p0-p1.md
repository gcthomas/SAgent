# 上下文压缩 P0/P1 问题修复方案

## 摘要

针对此前分析报告中的 3 项 P0（正确性缺陷）与 3 项 P1（摘要质量缺陷），已逐项回到源码核验，**全部属实**。本方案给出针对性修改：3 处源码文件修改（`strategies.py`、`context_manager.py`、`prompts.py`），2 个既有测试更新，5 个新增测试。不涉及配置、引擎、会话管理等其他模块。

---

## 一、审视确认结论（Current State Analysis）

### P0-1 第三层摘要切分未对齐 tool pair —— 属实

- 位置：[context_manager.py](src/sagent/context/context_manager.py#L225-L229)
- 现状：`recent = body[-keep_count:]` 切点无任何边界对齐。若切点落在 tool 结果消息上，重组 `system_msgs + [summary_msg] + recent` 后，`recent` 以孤儿 tool 消息开头（父 assistant(tool_calls) 已被摘要替换），严格端点返回 400。
- 同样缺陷存在于 [strategies.py](src/sagent/context/strategies.py#L440-L441) `LLMSummaryCompression.compress`（独立公共入口，与 ContextManager 第三层逻辑重复）。
- 参照：同文件 `SlidingWindowPruning.compress`（L321-326）已有正确的对齐实现可镜像。

### P0-2 第二层卸载产生孤儿 tool 消息 —— 属实

- 位置：[strategies.py](src/sagent/context/strategies.py#L230-L245)
- 现状：卸载时 `offloaded_assistant.pop("tool_calls")`，但仍为每条 tool 结果生成 `role="tool"` 的 `[已卸载: ...]` 占位消息 → 孤儿 tool 消息。
- 关键事实：tool 结果 content 此时已被替换为零信息的标记文本，删除这些占位消息不产生额外信息损失。
- 测试陷阱：`test_offload_old_tool_messages_replaced`（[test_strategies.py](tests/unit/test_strategies.py#L63-L96)）当前断言 `result[2]["role"] == "tool"`，固化了错误行为，必须同步更新。

### P0-3 内部字段 seq 泄漏到 LLM API —— 属实

- 泄漏链路：`get_messages()` 返回带 `seq` 的消息（[context_manager.py](src/sagent/context/context_manager.py#L88-L96)）→ [react_engine.py](src/sagent/core/react_engine.py#L161-L162) 直接透传 → [client.py](src/sagent/llm/client.py#L86-L89) 原样进入 `chat.completions.create(**kwargs)`，无字段剥离。
- `seq` 是会话持久化的内部元数据（[session/manager.py](src/sagent/session/manager.py#L299) 显式依赖），不属于 OpenAI 消息格式：严格端点拒绝未知字段，宽松端点额外计费。
- 已核验的约束：`export_new_messages` / `load_messages` / `_compress` 回调的 covered_seq 均直接读 `self._messages`，不受 `get_messages` 改动影响；session 持久化走 `export_new_messages`，不经过 `get_messages`；`plan_engine` 的 `_decompose`/`_summarize` 自建消息。
- 测试依赖：`test_seq_preserved_in_get_messages` 与 `test_export_new_messages_returns_copies`（L272-274）依赖 `get_messages` 返回 seq，需同步更新。

### P1-4 摘要输入丢失 tool_calls 信息 —— 属实

- 位置：[strategies.py](src/sagent/context/strategies.py#L378-L390)
- 现状：`summarize()` 只取 `content`，assistant 的 tool_calls（工具名/参数）对摘要 LLM 完全不可见；且所有角色统一截 200 字符，无差异化预算（tool 结果承载事实输出，应有更大预算）。

### P1-5 摘要提示词非结构化 —— 属实

- 位置：[prompts.py](src/sagent/context/prompts.py#L7-L14)
- 现状：单段自由文本，无分段输出结构，无 verbatim 保留文件路径/命令/代码签名的要求（对标 Claude Code compaction 实践）。

### P1-6 摘要长度无硬校验 —— 属实

- 位置：[strategies.py](src/sagent/context/strategies.py#L409-L418)
- 现状：`summary_max_tokens`（默认 500）仅注入提示词约束 LLM 输出，收到响应后无校验；LLM 不遵守时超长摘要直接进入上下文。

---

## 二、修改方案（Proposed Changes）

### 修改 1（P0-2）：ToolMessageOffload 卸载时删除对应 tool 结果消息

文件：`src/sagent/context/strategies.py`

删除 L237-245 的「卸载对应的 tool 结果消息」循环（不再生成 `[已卸载]` 占位消息），原位置替换为注释说明：

```python
# 不保留对应的 tool 结果消息：父消息的 tool_calls 已被移除，
# 保留 role="tool" 的消息会形成孤儿 tool 消息，导致严格端点返回 400。
# 工具名/参数摘要与结果字符数已包含在上方 assistant 占位文本中。
idx = j
```

- assistant 占位文本 `[已压缩: {tool_name_str}({arg_str}), 结果 {total_result_chars} 字符]` 保持不变（保留原逻辑）。
- 同步更新类 docstring：说明过期对的 assistant 替换为摘要（移除 tool_calls），对应 tool 结果消息被删除。
- 附带影响（可接受，见「假设与决策」）：被卸载且尚未持久化的 tool 消息不再落盘；其原 content 本就被替换为零信息标记，无实际信息损失。

### 修改 2（P0-1）：第三层切分边界对齐 tool pair

文件 1：`src/sagent/context/context_manager.py` L225-229，替换为（镜像 `SlidingWindowPruning` 的对齐模式）：

```python
system_msgs, body = self._split_system_and_body()
keep_count = self._config.keep_recent_messages
# 切分边界对齐：若切点落在 tool 结果消息上，向前扩展以包含其
# 父 assistant(tool_calls) 消息，避免摘要后残留孤儿 tool 消息
cut = max(0, len(body) - keep_count)
while cut > 0 and body[cut].get("role") == "tool":
    cut -= 1
recent = body[cut:]
old = body[:cut]
```

（`len(body) <= keep_count` 时 `cut=0`，`recent=body`、`old=[]`，与原逻辑等价；`covered_from_seq/to_seq` 取自 `old` 首尾，不受影响。）

文件 2：`src/sagent/context/strategies.py` `LLMSummaryCompression.compress` L440-441，`recent/old` 两行替换为相同的 cut 对齐逻辑（保留前后两个提前返回 guard 不变）。

### 修改 3（P0-3）：get_messages 剥离内部字段 seq

文件：`src/sagent/context/context_manager.py` L88-96

```python
def get_messages(self) -> list[dict[str, Any]]:
    """获取当前消息列表（已剥离内部元数据字段，可直接发送给 LLM）。

    如果 token 超阈值，自动触发压缩后返回。

    返回:
        消息列表的副本（不含内部字段 seq）
    """
    if self._is_over_threshold():
        self._compress()
    # 剥离内部元数据字段 seq：该字段用于会话持久化（export_new_messages /
    # load_messages 直接读内部列表，不受影响），不属于 OpenAI 消息格式，
    # 泄漏到 API 请求会被严格端点拒绝并产生额外计费
    return [{k: v for k, v in m.items() if k != "seq"} for m in self._messages]
```

### 修改 4（P1-4）：summarize 输入渲染 tool_calls + 角色差异化预算

文件：`src/sagent/context/strategies.py` `summarize()` L378-390

模块顶部新增常量：

```python
# 摘要输入中各角色消息的预览字符预算
_TEXT_PREVIEW_CHARS = 200  # user/assistant/system 普通文本
_TOOL_RESULT_PREVIEW_CHARS = 500  # tool 结果（承载事实输出，预算更大）
```

消息格式化循环改为：

```python
for msg in old_messages:
    role = msg.get("role", "unknown")
    content = msg.get("content") or ""
    # 角色差异化预览预算：tool 结果承载事实输出，保留更长预览
    budget = _TOOL_RESULT_PREVIEW_CHARS if role == "tool" else _TEXT_PREVIEW_CHARS
    preview = content[:budget]
    if len(content) > budget:
        preview += "..."
    lines.append(f"[{role}] {preview}")
    # assistant 的 tool_calls 是关键执行记录（工具名/参数），需呈现给摘要 LLM；
    # 兼容嵌套（OpenAI 标准）与扁平（conftest.make_tool_call）两种格式，
    # 与 ToolMessageOffload 的解析方式保持一致
    if role == "assistant" and msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
            name = func.get("name", "") or tc.get("name", "") or ""
            args_str = func.get("arguments", "") or tc.get("arguments", "") or ""
            if not name:
                continue
            args_preview = args_str[:_TEXT_PREVIEW_CHARS]
            if len(args_str) > _TEXT_PREVIEW_CHARS:
                args_preview += "..."
            lines.append(f"[assistant 工具调用] {name}({args_preview})")
```

### 修改 5（P1-5）：结构化摘要提示词

文件：`src/sagent/context/prompts.py`，重写 `SUMMARY_SYSTEM_PROMPT`（保持 `{max_tokens}` 占位符与 `.format()` 调用方式不变，`strategies.py` L393 无需改动）：

```python
SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要助手。请把以下对话历史（可能已含历史摘要）压缩成结构化的事实性摘要。\n"
    "要求：\n"
    "1. 逐字保留（verbatim）：文件路径、命令行、代码签名（函数名/类名）、错误信息、关键数字与版本号。\n"
    "2. 概括保留：用户目标、已完成的动作与结果、已确认的关键事实、未解决的约束。\n"
    "3. 丢弃：寒暄、重复内容、已被推翻的中间结论、工具调用的原始长输出。\n"
    "4. 按以下结构分段输出（无内容的段落可省略）：\n"
    "[用户目标]\n"
    "[已完成动作与结果]\n"
    "[关键事实与约束]\n"
    "[未决事项]\n"
    "5. 使用简洁的中文，摘要不超过 {max_tokens} 个 token。\n"
    "6. 输出纯文本摘要，不要输出 JSON 或其他格式。"
)
```

### 修改 6（P1-6）：摘要长度硬校验

文件：`src/sagent/context/strategies.py`，`LLMSummaryCompression` 新增私有方法，`summarize()` 返回前调用：

```python
def _enforce_summary_limit(self, summary: str) -> str:
    """硬校验摘要长度：超过 summary_max_tokens 时按比例截断。

    参数:
        summary: LLM 生成的摘要文本
    返回:
        满足 token 上限的摘要文本（截断时追加标记）
    """
    if not summary:
        return summary
    marker = "\n...[摘要已截断至 token 上限]"
    tokens = count_text_tokens(summary + marker, self._model)
    if tokens <= self._summary_max_tokens:
        return summary
    logger.warning(
        "摘要超过 token 上限，已截断",
        extra={
            "event": "summary_truncated",
            "summary_max_tokens": self._summary_max_tokens,
            "original_tokens": tokens,
        },
    )
    # 按 token 占比估算字符保留量，每轮预留 10% 余量，循环收缩直至满足上限
    while tokens > self._summary_max_tokens and summary:
        keep_chars = max(1, int(len(summary) * self._summary_max_tokens / tokens * 0.9))
        summary = summary[:keep_chars]
        tokens = count_text_tokens(summary + marker, self._model)
        if keep_chars == 1:
            break
    return summary + marker
```

`summarize()` 中 `summary = response.content or ""` 之后、日志之前插入 `summary = self._enforce_summary_limit(summary)`。

---

## 三、测试同步更新

### 更新既有测试

| 测试 | 文件 | 改动 |
|---|---|---|
| `test_offload_old_tool_messages_replaced` | tests/unit/test_strategies.py | 断言改为：结果共 4 条 `[system, 卸载后 assistant, user, assistant]`；不含任何 `role=="tool"` 消息；卸载后 assistant 仍含「已压缩」且无 tool_calls（原断言保留） |
| `test_seq_preserved_in_get_messages` | tests/unit/test_context_manager.py | 改写为 `test_get_messages_strips_internal_seq`：`get_messages()` 返回值不含 `seq` 键；`cm._messages` 中 seq 保留且为 1、2 |
| `test_export_new_messages_returns_copies` | tests/unit/test_context_manager.py | L272 起 `original = cm.get_messages()` 改为 `original = cm._messages`（该测试本意是验证导出副本不影响内部状态，seq 校验改读内部列表） |

### 新增测试

1. `test_summary_compress_aligns_tool_pair_boundary`（tests/unit/test_strategies.py，验证修改 2）
   - 直接调用 `LLMSummaryCompression.compress`，构造 `body = [u0, u1, a(tool_calls), t, u2]`、`keep_recent=2`（切点落在 t 上）
   - 断言：摘要消息后首条 body 消息为含 tool_calls 的 assistant；其 tool 结果紧随；用「每条 tool 消息的 tool_call_id 必须能在前序 assistant(tool_calls) 中找到」的校验器断言无孤儿
2. `test_compression_produces_no_orphan_tool_messages`（tests/unit/test_context_manager.py，修改 1+2 集成回归）
   - 小预算（max_context_tokens=300）+ enable_summary=True + keep_recent_messages=2，构造含两组 assistant(tool_calls)+tool 结果的对话
   - 压缩后断言：消息数减少、`cm._existing_summary` 非空（第三层确实执行）、同一孤儿校验器通过
3. `test_summary_renders_tool_calls_and_tool_result_budget`（tests/unit/test_strategies.py，验证修改 4）
   - old_messages 含 assistant(tool_calls)（扁平格式，`make_tool_call("read_file", {"path": "/tmp/a.txt"})`）与 300 字符 tool 结果
   - 断言发给摘要 LLM 的 user 消息（`llm.calls[0]["messages"][1]["content"]`）包含 `read_file`、`/tmp/a.txt`、以及至少 250 字符的 tool 结果内容（旧实现只截 200）
4. `test_summary_system_prompt_structured`（tests/unit/test_strategies.py，验证修改 5）
   - 断言发给摘要 LLM 的 system 消息包含 `[用户目标]`、`[已完成动作与结果]` 等分段标记与「逐字保留」要求
5. `test_summary_truncated_to_token_limit`（tests/unit/test_strategies.py，验证修改 6）
   - `summary_max_tokens=50`，FakeLLM 返回 2000 字符长文本
   - 断言：结果包含「已截断」标记；`count_text_tokens(result, "", "auto") <= 50`（与实现内部使用同一计数函数，保证确定性）

### 预期不受影响的既有测试（回归确认点）

- `test_compression_triggered` / `test_system_prompt_protected` / `test_context_manager_multi_turn_with_compression`：纯 user/assistant 消息，不涉 tool pair 与 seq 断言
- `test_compaction_callback_invoked_with_summary_and_seq_range` / `test_reset_clears_existing_summary`：body 全为 user 消息，对齐逻辑不改变切分结果，回调契约不变
- `test_pruning_keeps_tool_call_pair_intact` 等：第四层未被改动
- `test_session_manager.py` 全部：持久化走 `export_new_messages`（含 seq），`switch_session` 走 `load_messages`，均不经过 `get_messages` 的 seq 剥离
- `tests/engines/test_context_integration.py`：断言仅涉及 role/content

---

## 四、假设与决策（Assumptions & Decisions）

### 决策 1（P0-3 剥离位置）：在 `get_messages()` 剥离 seq（本方案采用）

理由：`get_messages` 是「发送给 LLM 的消息」的唯一语义出口（其自动触发压缩即为证明）；单一收口，未来新增引擎/调用方不会再次泄漏；持久化路径不经过它。
代价：同步更新 2 个测试（本就必要）。
备选（未采用）：
- react_engine 两处调用点剥离：不改契约，但收口不彻底，新增调用方会再泄漏
- client.chat 内剥离：收口最彻底，但 LLMClient 需感知上层内部字段名，层级耦合

### 决策 2（P0-2 tool 结果处理）：直接删除卸载对的 tool 结果消息（本方案采用）

理由：现有实现本就将 tool content 替换为零信息的 `[已卸载]` 标记，删除不产生额外信息损失，改动最小、与 assistant 占位文本语义自洽。
备选（未采用）：将结果预览合并进 assistant 占位文本——保留更多信息，但改变既有占位格式，且属范围外增强。

### 决策 3（修改 2 同时修两处）：ContextManager 第三层与 LLMSummaryCompression.compress 同步加对齐

理由：后者是被测试覆盖的独立公共入口，不经 ContextManager 流程，其缺陷独立成立；前者作为防御性对齐（镜像第四层既有模式，3 行改动），使 `_compress` 自身边界安全不依赖第二层的实现细节。两处不合并重构（重复逻辑保持现状，遵守「不做无关重构」）。

### 其他假设

- 修改 1 后，被卸载且尚未持久化的 tool 消息不再落盘：其 content 本就被替换为零信息标记，无实际信息损失；已持久化的消息不受影响，会话还原时父子消息成对出现，不产生孤儿。
- 修改 3 使 API 请求不再携带 seq，prompt_tokens 略降，混合校准基准随之更准（正向副作用）。
- 摘要 token 计数沿用 `count_text_tokens`（auto：优先 tiktoken，回退启发式），与现有实现一致，不新增配置项。

---

## 五、验证步骤（Verification）

1. 受影响范围：`python -m pytest tests/unit/test_strategies.py tests/unit/test_context_manager.py -v`
2. 回归：`python -m pytest tests/unit tests/engines`
3. 全量：`python -m pytest`

（真实 LLM 评测 `tests/evals` 需 `RUN_LLM_EVALS=1`，本次不涉及。）
