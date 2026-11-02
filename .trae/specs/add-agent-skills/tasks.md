# Tasks

- [x] Task 1: 建立 Skill 核心模型与发现器
  - [x] 定义 Skill 元数据、来源、校验结果和可用状态模型。
  - [x] 实现 `SKILL.md` frontmatter 解析、名称/目录/字段长度校验和 UTF-8 读取。
  - [x] 实现项目根目录 `.sagent/skills`、用户主目录 `.sagent/skills` 和配置 `skills.extra_dirs` 的直接子目录扫描；`.agents/skills` 仅在显式配置后接入，并实现 allow/deny 过滤和同名来源处理。
  - [x] 增加非法文件、不可读目录、重名和路径边界的单元测试。

- [x] Task 2: 增加 Skill 配置并接入应用装配
  - [x] 在配置模型中增加 `SkillConfig` 并接入 `AppConfig`。
  - [x] 更新 `config.example.yaml`，覆盖启用开关、目录、过滤和预算配置。
  - [x] 在 CLI 应用启动阶段构建 Skill 管理器，并保留现有工具、MCP、Memory 和权限装配顺序。
  - [x] 增加启动信息中的 Skill 状态展示。

- [x] Task 3: 实现 Skill 渐进式披露与加载工具
  - [x] 构建只包含 Skill 名称、描述、来源和调用约束的目录提示词。
  - [x] 实现统一的 `load_skill` Tool，限制只能加载已发现且可用的 Skill。
  - [x] 实现正文预算校验、Skill 根目录内的资源路径解析和路径逃逸拒绝。
  - [x] 确保加载 Skill 不自动执行脚本、不安装依赖、不调用 MCP。
  - [x] 增加 `load_skill` 的注册、权限、错误返回和观测测试。

- [x] Task 4: 接入 ReAct、Plan 和上下文管理
  - [x] 将 Skill 目录摘要独立注入 ReAct 系统提示词，并与 Memory 前缀共存。
  - [x] 确保 Plan 分解和每个步骤复用同一组 Skill 元数据与工具。
  - [x] 确保 Skill 正文通过 ContextManager 参与 token 统计、压缩和会话持久化。
  - [x] 增加上下文压缩后重新加载 Skill 的引擎测试。

- [x] Task 5: 增加 CLI 命令
  - [x] 扩展命令解析，支持 `/skills` 和 `/skill <name> [instruction]`。
  - [x] 实现 Skill 列表、来源、状态和诊断信息展示。
  - [x] 实现显式 Skill 调用的名称校验、正文加载和任务转交。
  - [x] 增加命令解析与分发测试，确保无效 Skill 不进入 LLM 执行。

- [x] Task 6: 完善日志、Span 和回归验证
  - [x] 为发现、过滤、加载成功和加载失败增加结构化日志。
  - [x] 增加 Skill 加载 Span，记录名称、来源、结果和正文大小，不记录正文全文。
  - [x] 运行受影响范围的 pytest，再运行完整 pytest。
  - [x] 检查现有 Tool、MCP、Memory、Permission、ReAct 和 Plan 测试无回归。

- [x] Task 7: 修复验收不通过项（来自 checklist 复核）
  - [x] 修正 ReAct 系统提示词叠加顺序：先叠加 skill_prefix 再叠加 memory_prefix，使 memory 保持最前（已由主会话修复，仅复验）。
  - [x] `/skill` 缺少 instruction 时不进入 LLM 执行，提示补充任务说明。
  - [x] Skill 名称歧义或未找到时，向用户透出 `SkillNotFoundError` 的完整消息（含候选列表）。
  - [x] `/help` 中的 skill 命令说明跟随 `skills.enabled` 门控展示。
  - [x] Skill 加载渲染全文的 token 预算与上下文侧 `max_tool_output_tokens` 对齐，超限拒绝并提示。
  - [x] Plan 分解阶段（`_decompose`）的 system 消息注入技能目录片段。
  - [x] 新增 `read_skill_resource` 工具：仅允许读取技能目录内的文件，路径规范化校验并拒绝符号链接/junction 逃逸；更新加载渲染中的资源读取指引。
  - [x] 为以上修复补充或更新单元与引擎测试，运行受影响范围与完整 pytest 回归。

# Task Dependencies

- Task 2 depends on Task 1.
- Task 3 depends on Task 1 and Task 2.
- Task 4 depends on Task 2 and Task 3.
- Task 5 depends on Task 1 and Task 2; it can run in parallel with Task 3 and Task 4 after those dependencies are complete.
- Task 6 depends on Tasks 3, 4 and 5.
- Task 7 depends on Tasks 1-6（验收复核后的修复任务）。
