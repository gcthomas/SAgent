# 修复 Plan 投影泄漏下游步骤（方案 A + C + F）

## 摘要

修复 `PlanState.projection()` 把全部 pending 步骤的完整可执行描述写进步骤请求、导致当前步骤越界执行的问题。采用三项组合：

- **A（title/detail 拆分）**：`PlanStepState` 增加 `title`（目标短句，不含工具/平台/检索手段），投影只输出标题；`description` 保留完整执行说明，仅用于当前步骤的 `step_task`。
- **C（lookahead=1）**：投影只暴露"下一步"这一个标题，其余待执行步骤仅给出数量。
- **F（边界约束）**：投影末尾追加"不得提前执行后续步骤、不得为后续步骤调用工具"的约束语句。
- **去重**：投影删除"当前步骤"行，它与 `step_task` 中的"当前需要完成的步骤: ..."完全重复。

投影的**角色与位置保持不变**：仍作为单次请求副本的末尾 system 消息，不写入历史。已确认不改为 assistant，也不改为持久化 user 消息（理由见"投影角色与位置的决策"）。

本次不改动执行链路、ReAct 引擎与重规划上限；仅改计划描述契约、投影内容与配套测试/文档。

## 当前状态分析

`PlanState.projection()`（[plan_state.py](../../src/sagent/core/plan_state.py#L47-L62)）当前：

```python
remaining = [step.description for step in self.steps if step.status == "pending"]
...
f"剩余步骤: {' | '.join(remaining[:10])[:1200]}\n"
```

- 步骤 1 执行时步骤 2-9 全为 `pending`，8 条完整描述（含"通过天眼查、企查查查询…"等具体手段）全部注入请求末尾 system 消息。
- 默认 `max_steps=10`，本次 9 步，`[:10]` / `[:1200]` 均未触发截断。
- 该投影由 [react_engine.py](../../src/sagent/core/react_engine.py#L255-L260) 作为**末尾 system 消息**附加，指令性最强，直接放大了泄漏效果。

相关现状：

- `PlanStepState` 只有 `description` 一个描述字段（[plan_state.py](../../src/sagent/core/plan_state.py#L13-L20)）。
- 拆解提示词要求"清晰、独立、可执行"，输出 `{"steps": ["..."]}`（[prompts.py](../../src/sagent/core/prompts.py#L22-L28)），因此步骤描述天然是"动作+手段"。
- `_parse_steps` 返回 `list[str]`（[plan_engine.py](../../src/sagent/core/plan_engine.py#L390-L411)），被 `_decompose()` 与 `_replan()` 共用。
- `PlanStepState` 通过 `dataclass` 与 `asdict` 序列化，`PlanState.from_dict` 使用 `PlanStepState(**step)`（[plan_state.py](../../src/sagent/core/plan_state.py#L68-L73)），新增带默认值的字段可兼容旧快照。
- 现有测试大量使用 `{"steps": ["步骤一", ...]}` 纯字符串契约：`tests/unit/test_plan_parse.py`、`tests/engines/test_plan_engine.py`、`test_agent_engine.py`、`test_observability_engines.py`、`test_context_integration.py`、`tests/evals/test_agent_evals.py`。

### 投影角色与位置的决策

现状：[react_engine.py](../../src/sagent/core/react_engine.py#L255-L261) 每轮迭代把投影追加到 `cm.get_messages()` 的副本，不调 `add_message`，因此不进入历史；无上下文管理器路径同样在 user 任务后追加一条 system（[react_engine.py](../../src/sagent/core/react_engine.py#L117-L122)）。

保持"末尾 system、请求本地"的三条依据：

1. 投影含 `current_step` 与 `recent_observation`，每步动态生成；持久化会让会话中不断累积计划快照。
2. 末尾 system 是指令权重最高的位置，与"只做当前步骤"的约束目标一致。
3. 该结构在 `persist_task=True` 修复后已通过测试与真实交互验证，改动无收益且引入回归风险。

评估后放弃的两种替代：

- **改为 assistant**：会复现本次修好的空响应结构（历史以 assistant 结尾时模型认为本轮已作答）；把外部约束伪装成模型自我陈述会削弱遵从；历史常态以 `tool` 结尾，插入 assistant 后部分 OpenAI 兼容端按"续写"处理而非重新作答；一旦持久化还会污染可审计历史。
- **改为持久化 user 投影**：每步向历史写入一条长投影，累积占用上下文预算，且与 `step_task` 的职责重叠。

## 实施改动

### 1. `src/sagent/core/plan_state.py`

**改动 1：`PlanStepState` 增加 `title` 字段。**

```python
@dataclass
class PlanStepState:
    """单个计划步骤的可审计状态。"""

    description: str
    title: str = ""
    status: StepStatus = "pending"
    summary: str = ""
    artifact_refs: list[str] = field(default_factory=list)
```

- `description` 保持在首位，所有现存构造点均使用关键字参数，不会破坏位置参数语义。
- `title` 给默认值，保证 `PlanState.from_dict` 对旧快照（无 `title` 键）仍可构造。

**改动 2：重写 `projection()`。**

```python
    def projection(self, recent_observation: str = "") -> str:
        """生成有界计划投影，供单次 LLM 请求使用。

        只暴露执行进度与下一步标题，不列出后续步骤的完整描述，也不重复
        step_task 已携带的当前步骤，避免当前步骤把下游步骤当作本轮任务
        而越界执行。
        """
        completed = [step.summary[:300] for step in self.steps if step.status == "completed"]
        pending = [step for step in self.steps if step.status == "pending"]
        observation = recent_observation or self.recent_observation
        artifacts = self.artifact_refs[-10:]
        next_title = pending[0].title if pending else "无"
        return (
            "[计划投影]\n"
            f"原始任务: {self.task[:500]}\n"
            f"已完成摘要: {' | '.join(completed[-5:])[:1200]}\n"
            f"执行进度: 共 {len(self.steps)} 步，已完成 {len(completed)} 步，待执行 {len(pending)} 步\n"
            f"下一步: {next_title[:80]}\n"
            f"最近观察: {observation[-1200:]}\n"
            f"产物引用: {' | '.join(artifacts)[:800]}\n"
            "约束: 进度与下一步仅用于了解整体安排，不得提前执行后续步骤，"
            "也不得为后续步骤调用工具；请只完成当前步骤。"
        )
```

要点：

- 删除 `remaining` 全量描述行，改为"执行进度"计数行 + "下一步"单个标题。
- 删除"当前步骤"行：步骤描述已由 `step_task` 以 user 消息给出（[plan_engine.py](../../src/sagent/core/plan_engine.py#L224-L227)），保留会重复占用预算并稀释约束句。
- lookahead 固定为 1，不新增配置项。
- 约束语句放在投影末尾，保证它是模型读到的最后一条指令。
- 原始任务、已完成摘要、最近观察、产物引用及其截断上限全部保留。`current_step` 字段本身不删除，其他逻辑不受影响。

### 2. `src/sagent/core/prompts.py`

**改动：拆解与重规划提示词改为对象契约，并约束 `title` 的语义。**

```python
# Plan 模式：任务拆解提示词
PLAN_DECOMPOSE_PROMPT = (
    "你是一个任务规划助手。请把用户的复杂任务拆解为有序、可执行的步骤。\n"
    "要求：\n"
    "- 每个步骤包含 title 和 description 两个字段。\n"
    "- title 是简短目标（不超过 20 字），只描述本步骤要达成的结果或产出物，"
    "不得包含具体工具、网站、平台或检索手段。\n"
    "- description 是完整可执行说明，可包含方法、工具和数据来源。\n"
    "- 只输出 JSON，格式为 {\"steps\": [{\"title\": \"...\", \"description\": \"...\"}]}。\n"
    "- 不要输出任何除 JSON 之外的解释文字。"
)

# Plan 模式：失败后重规划提示词
PLAN_REPLAN_PROMPT = (
    "你是一个计划修复助手。请根据原始任务、已完成步骤、剩余步骤和失败观察，"
    "重新生成剩余的可执行步骤。\n"
    "已完成步骤必须保留，不要重复已完成工作；每个步骤包含 title 和 description "
    "两个字段，title 为不超过 20 字的目标短句，不得包含具体工具、网站、平台或检索手段。\n"
    "只输出 JSON，格式为 {\"steps\": [{\"title\": \"...\", \"description\": \"...\"}]}。"
)
```

`title` 不含手段是方案 A 生效的前提：若标题仍写"通过天眼查查询股东结构"，投影依旧泄漏执行方法。

### 3. `src/sagent/core/plan_engine.py`

**改动 1：`_parse_steps` 返回 `(title, description)` 二元组列表。**

```python
    @staticmethod
    def _parse_steps(content: str) -> list[tuple[str, str]]:
        """从 LLM 输出中解析 (title, description) 步骤列表。

        兼容返回内容被 ```json 代码块包裹的情况；缺少 title 或 description
        的条目视为无效并丢弃。解析失败返回空列表。
        """
        text = content
        if text.startswith("```"):
            text = text.strip("`")
            newline = text.find("\n")
            if newline != -1 and text[:newline].strip().lower() in ("json", ""):
                text = text[newline + 1 :]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps, list):
            return []
        parsed: list[tuple[str, str]] = []
        for item in steps:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            if title and description:
                parsed.append((title, description))
        return parsed
```

- 严格只接受对象条目，不兼容纯字符串（遵循项目"不考虑 fallback"约定）。模型返回旧格式时解析为空，走既有"拆解失败回退 ReAct"路径。

**改动 2：`_decompose` 返回类型与说明同步。**

```python
    def _decompose(self, task: str) -> list[tuple[str, str]]:
        """调用 LLM 将任务拆解为 (title, description) 列表。解析失败返回空列表。"""
```

**改动 3：`run()` 中拆解结果处理。**

```python
        else:
            with Span("plan.decompose") as decompose_span:
                step_specs = self._decompose(task)
            step_specs = step_specs[: self._planning_config.max_steps]
            steps = [description for _, description in step_specs]
```

`steps` 仍为 `list[str]`，`len(steps)`、`if not steps`、步骤通知、日志等既有逻辑全部不变；计划状态构造改为：

```python
            self.plan_state = PlanState(
                version=1,
                task=task,
                steps=[
                    PlanStepState(title=title, description=description)
                    for title, description in step_specs
                ],
                status="executing",
            )
```

**改动 4：`_replan()` 同步为二元组契约。**

```python
        remaining = [
            (step.title, step.description)
            for step in state.steps[failed_index - 1 :]
        ]
```

重规划提示词中的"剩余步骤"仍输出 description 列表（规划器需要完整信息）：

```python
                        f"剩余步骤:\n{[description for _, description in remaining]}\n"
```

解析与重建：

```python
        revised = self._parse_steps((response.content or "").strip())
        if not revised:
            revised = remaining
        revised = revised[: self._planning_config.max_steps]
        state.steps = completed + [
            PlanStepState(title=title, description=description)
            for title, description in revised
        ]
```

`if not revised: revised = remaining` 保留原有"解析失败沿用原步骤"行为，不是新增兜底。

### 4. 测试

**新增 `tests/unit/test_plan_state.py`**（当前不存在该文件）：

- `test_projection_hides_pending_descriptions`：构造 4 步计划，步骤 1 为 running、后 3 步 pending；断言投影不含步骤 2/3/4 的 `description`。
- `test_projection_exposes_only_next_step_title`：断言投影含"下一步: <步骤 2 标题>"，不含步骤 3/4 标题。
- `test_projection_reports_progress_and_boundary`：断言含"共 4 步，已完成 0 步，待执行 3 步"、"不得提前执行"，且不含"剩余步骤"。
- `test_projection_omits_current_step_and_keeps_observation`：断言投影不含"当前步骤:"行、不含步骤 1 的 `description`，但 `recent_observation` 仍正确输出，且"原始任务"行保留。
- `test_projection_without_pending_step`：全部 completed 时输出"下一步: 无"。

**改写 `tests/unit/test_plan_parse.py`**：全部用例改用对象契约，断言返回二元组：

```python
assert PlanEngine._parse_steps(
    '{"steps": [{"title": "a", "description": "a1"}, {"title": "b", "description": "b1"}]}'
) == [("a", "a1"), ("b", "b1")]
```

并保留/新增：代码块包裹、非法 JSON、缺 `steps` 键、空字段过滤（`title` 或 `description` 为空则丢弃）、纯字符串条目被丢弃。

**更新现有拆解桩数据**（仅把纯字符串列表替换为对象列表，语义不变）：

- `tests/engines/test_plan_engine.py`：全部 `text_response('{"steps": [...]}')`。
- `tests/engines/test_agent_engine.py`、`tests/engines/test_observability_engines.py`、`tests/engines/test_context_integration.py` 中的拆解响应。
- `tests/evals/test_agent_evals.py` 第 292 行的拆解响应，以及 `_run_plan_experiment` 中 `engine._decompose = lambda _task: list(placeholder_steps)` 改为返回 `[(step, step) for step in placeholder_steps]`（该文件为未提交的诊断脚本，仅做机械适配，不改实验语义）。

**新增 `tests/engines/test_plan_engine.py::test_plan_projection_exposes_only_next_step_title`**：

- 拆解为 3 步（标题一/二/三，描述一/二/三），每步直接作答，最后汇总。
- 取 `llm.calls[1]["messages"][-1]`（步骤 1 的投影）断言：
  - 含 `下一步: 标题二`
  - 不含 `标题三`、`描述一`、`描述二`、`描述三`
  - 含 `不得提前执行`
  - 仍以 `[计划投影]` 开头
- 同一次断言 `llm.calls[1]["messages"][-2]` 是 user 角色且含 `当前需要完成的步骤: 描述一`，确认当前步骤信息只出现一次。

### 5. `docs/architecture.md`

第 116 行 Plan 执行说明补充投影内容边界：

> 以 `plan_projection` 传入计划状态投影（作为当次请求副本的末尾 system 消息、不写入历史），投影只暴露执行进度与下一步标题并附带禁止越界约束，不含当前步骤（由步骤指令携带），避免下游步骤描述被提前执行；并将步骤指令作为 user 消息持久化（`persist_task=True`）……

## 假设与决策

1. **严格对象契约，不兼容纯字符串**：`_parse_steps` 只接受 `{"title", "description"}` 对象。依据项目约定"不考虑 fallback"；旧格式解析为空后走既有拆解失败回退。
2. **lookahead 固定为 1，不新增配置项**：避免为单一取值引入 `PlanningConfig` 字段。`max_steps` 等既有配置不变。
3. **`title` 缺省为空字符串**：保证 `PlanState.from_dict` 可读取旧会话快照。旧快照恢复后"下一步"行可能为空，不新增兼容分支。
4. **`description` 语义不变**：仍是完整可执行说明，用于 `step_task`、重规划输入、步骤通知与日志；投影不再读取它。
5. **投影角色与位置不变**：保持请求副本末尾 system、不持久化。已评估并放弃 assistant 与持久化 user 两种替代，理由见"投影角色与位置的决策"。
6. **投影不再输出当前步骤**：该信息由 `step_task` 以 user 消息唯一承载，避免同一步骤描述在请求中出现两次。
7. **不改执行链路**：不动 `ReActEngine`、`persist_task`、重规划上限与汇总逻辑。

## 实施进度（续做状态）

已完成并已核对源码/测试：

- `src/sagent/core/plan_state.py`：`PlanStepState.title` 已加入；`projection()` 已重写为"原始任务 / 已完成摘要 / 执行进度 / 下一步标题 / 最近观察 / 产物引用 / 约束"。
- `src/sagent/core/prompts.py`：`PLAN_DECOMPOSE_PROMPT`、`PLAN_REPLAN_PROMPT` 已改为 `title + description` 对象契约。
- `src/sagent/core/plan_engine.py`：`_parse_steps`/`_decompose` 返回 `list[tuple[str, str]]`；`run()` 使用 `step_specs` 构造 `PlanState`；`_replan()` 已同步二元组。
- `tests/unit/test_plan_state.py`（新增 5 用例）、`tests/unit/test_plan_parse.py`（改写 7 用例）、`tests/engines/test_plan_engine.py`（桩 + 新增投影用例）、`tests/engines/test_agent_engine.py`（3 处桩）、`tests/engines/test_context_integration.py`（1 处桩）均已完成。

剩余步骤（严格按本计划执行，不新增设计）：

1. `tests/engines/test_observability_engines.py` 第 307 行：`text_response('{"steps": ["步骤一", "步骤二"]}')` → `text_response('{"steps": [{"title": "标题一", "description": "步骤一"}, {"title": "标题二", "description": "步骤二"}]}')`（description 保持"步骤一/步骤二"，该用例只断言 span 名称与 step_index/step_total）。
2. `tests/evals/test_agent_evals.py` 第 292 行：`text_response('{"steps": ["收集信息", "整理信息"]}')` → 对象契约，description 保持"收集信息/整理信息"（第 301-302 行断言依赖步骤结果文本）。
3. `docs/architecture.md` 第 115 行：替换为计划中第 5 节的表述。
4. 运行验证：
   - `python -m pytest tests/unit -q`
   - `python -m pytest tests/engines -q`
   - `python -m pytest -q`
   失败项按计划范围内修复，不扩大改动。

## 验证

1. 受影响范围：
   - `python -m pytest tests/unit -q`
   - `python -m pytest tests/engines -q`
2. 全量离线测试：`python -m pytest -q`（`tests/evals` 中 `llm_eval` 用例默认跳过）。
3. 人工核对（可选，需真实 LLM）：运行一次多步骤调研任务，确认步骤 1 的请求中 `[计划投影]` 只含"原始任务 + 已完成摘要 + 执行进度 + 下一步标题 + 最近观察 + 产物引用 + 约束"，不含当前步骤描述与任何下游描述，且步骤 1 不再发起后续步骤职责的检索。
