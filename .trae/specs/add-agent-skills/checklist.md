# 验收清单

## 格式与发现
- [x] 合法 UTF-8 `SKILL.md` 能被发现，必填字段及名称与目录一致性通过校验。
- [x] `name` 长度为 1–64，仅包含小写 ASCII 字母、数字、单连字符，不以连字符开头或结尾；`description` 长度为 1–1024。
- [x] 可选 `license`、`compatibility` 和 `metadata` 不破坏核心格式兼容性；实验性的 `allowed-tools` 不产生授权。
- [x] 发现阶段仅保留元数据，不向模型注入正文或读取、执行资源文件。
- [x] 项目根目录 `.sagent/skills`、用户主目录 `.sagent/skills` 和配置额外目录均可发现；系统只扫描这些根目录的直接子目录。
- [x] `.agents/skills` 不会被默认扫描，只有显式加入 `skills.extra_dirs` 后才会接入。
- [x] 每个直接子目录按 `SKILL.md` 校验，过滤和同名来源选择行为确定且可诊断。
- [x] 不存在的默认目录不阻断启动；非法或不可读 Skill 有诊断且不进入模型可用列表。

## 渐进式披露与执行边界
- [x] 模型工具列表只增加统一 `load_skill`，不会为每个 Skill 增加独立工具 Schema。
- [x] `load_skill` 只接受已发现的技能标识，不接受任意绝对文件路径。
- [x] 正文返回包含技能来源、根目录及相对资源路径说明。
- [x] 超过正文预算明确报错，不静默截断步骤；有效正文不会刚加载就被通用工具输出截断。（修复：load() 对渲染后全文做输出预算校验，并通过 token_counter 注入与上下文侧截断完全相同的计数口径）
- [x] 资源按需读取；受限的资源读取路径拒绝 `..`、符号链接和 Windows junction 等逃逸方式。（修复：新增 read_skill_resource 工具与 SkillLoader.load_resource，逐级拒绝 symlink/junction + relative_to 越界拒绝）
- [x] 资源路径检查不会被宣称为现有通用文件工具或 Shell 的文件系统沙箱。
- [x] 加载 Skill 不自动执行脚本、不安装依赖、不启动 MCP、不解释动态命令插值。
- [x] 所有后续工具调用继续经过 ToolRegistry 和原有权限策略；Skill 自身声明不能提升权限。

## 上下文与模式一致性
- [x] Memory 前缀保持原有语义，Skill 目录以独立片段装配。（修复：react_engine.py 先叠 skill_prefix 再叠 memory_prefix，test_react_memory_prefix_precedes_skill_prefix 转绿）
- [x] 注入与未注入 ContextManager 的 ReAct 路径均支持 Skill。
- [x] Plan 分解、步骤执行路径均能获取相同的可用技能目录，计划状态语义不变。（修复：_decompose 的 system 消息在 skill_prefix 非空时叠加技能目录，含正反两个引擎测试）
- [x] Skill 正文参与现有上下文预算和压缩，不永久豁免压缩。
- [x] 正文压缩后允许重新加载，不能仅凭历史加载标记拒绝再次读取。
- [x] 会话切换不会错误复用其他会话的任务级 Skill 状态。

## CLI 与配置
- [x] `/skills` 展示来源、描述、可用状态和诊断信息。
- [x] `/skill <name> <instruction>` 在进入任务执行前确保 Skill 正文加载，不依赖模型自行决定是否加载。
- [x] 显式调用中的未知、冲突、不可用技能在 LLM 调用前返回明确错误。（修复：SkillNotFoundError 的完整消息透出，短名歧义时含候选限定名列表）
- [x] 不带 instruction 的显式调用明确提示补充任务，不猜测并执行技能中的高风险操作。（修复：缺 instruction 时提示"请提供任务说明"并返回，不再合成占位任务）
- [x] `skills.enabled: false` 时不扫描目录、不注入目录、不注册加载工具，Skill 命令说明功能未启用。（修复：/help 的 skill 命令说明随 skills_enabled 门控展示）
- [x] 配置模型、加载测试和示例配置保持一致；预算配置有明确单位和有效范围。
- [x] Windows 路径、中文目录和中文正文保持 UTF-8，无乱码。

## 可观测性与回归
- [x] 发现与加载具有结构化日志和加载 Span，记录来源、大小、结果及失败原因，不默认记录正文全文。
- [x] 新增单元测试与 FakeLLMClient 引擎测试离线运行，响应数量与预期 LLM 调用轮次匹配。
- [x] 受影响范围测试先通过，再运行完整 pytest；未运行或失败原因明确记录。（最终：unit+engines 637 项 0 失败 1 skip；全量 649 项 0 失败 2 skip，skip 为按设计跳过）
- [x] 现有 Tool、MCP、Memory、Permission、Session、ReAct 和 Plan 的正确功能不发生无关变更。
- [x] 未引入自动安装、市场、自修改、子 Agent 执行或动态工具注册等本阶段之外的功能。
