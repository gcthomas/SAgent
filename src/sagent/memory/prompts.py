"""长期记忆相关提示词。

定义三类提示词：
- MEMORY_GUIDE_PROMPT：记忆使用引导，注入到前缀最前面，引导 LLM 何时/如何提取
  和记录记忆（参考 Hermes Agent 的记忆引导实践）。
- REFLECT_SYSTEM_PROMPT：反思整理提示词，记忆文件超限时调用 LLM 做去重、
  合并冗余与精简表述。
- build_injection_prefix：会话开始前注入的系统提示词前缀构造逻辑，将引导提示词、
  USER.md 与 MEMORY.md 内容拼装为可读前缀，整个会话期间冻结以命中 prefix cache。
"""

from __future__ import annotations

# 记忆使用引导提示词
# 会话开始时注入到记忆前缀最前面，引导 LLM 自主提取和记录记忆。
# 参考 Hermes Agent 的记忆引导实践：两个文件用途说明、什么该保存/跳过、写入原则。
# 作为冻结前缀的一部分，整个会话不变，不影响 prefix cache 命中。
MEMORY_GUIDE_PROMPT = (
    "# 长期记忆使用指引\n"
    "你可通过 add_memory / replace_memory / remove_memory 工具自主管理长期记忆。\n\n"
    "两个记忆文件：\n"
    "- USER.md（target=user）：用户档案——姓名、角色、时区、沟通偏好、反感事项、技术水平\n"
    "- MEMORY.md（target=memory）：Agent 笔记——环境事实、项目约定、工具怪癖、"
    "已完成工作、经验教训\n\n"
    "工具使用时机：\n"
    "- add_memory：出现值得长期记住的新内容时追加\n"
    "- replace_memory：已有记忆过时或需更正时，用 new 替换 old（old 需在现有记忆中精确匹配）\n"
    "- remove_memory：记忆已失效或不再适用时删除指定内容\n\n"
    "什么该保存：\n"
    '- 用户偏好 → user（如“偏好 TypeScript 而非 JavaScript”）\n'
    '- 环境事实 → memory（如“本机运行 Ubuntu 22.04，已安装 Docker”）\n'
    '- 纠正信息 → memory（如“Docker 命令不用 sudo，用户已在 docker 组”）\n'
    '- 项目约定 → memory（如“使用 tab 缩进、120 字符行宽”）\n'
    "- 已完成的重要工作 → memory\n"
    "- 用户的明确记忆请求 → memory\n\n"
    "什么该跳过：琐碎无实用价值的信息、可轻易搜索到的事实、大型代码/日志/数据、"
    "会话临时上下文。\n\n"
    "写入原则：追求信息密度，一条记忆打包多个相关事实；简洁具体，不空泛不啰嗦；"
    "写入前判断是否与已有记忆重复或可合并，优先 replace_memory 更新而非追加重复；"
    "记忆有字符上限，写满时先整合现有条目再添加新的。"
)

# 反思整理系统提示词模板
# 占位符 {content} 为待整理的记忆全文，{max_chars} 为整理后内容的字符上限
REFLECT_SYSTEM_PROMPT = (
    "你是长期记忆整理助手。请对以下记忆全文做去重、合并冗余与精简表述，输出整理后的 Markdown。\n"
    "要求：\n"
    "- 去除重复与矛盾信息，合并相似条目。\n"
    "- 保留关键事实、用户偏好、项目约定与学习经验，不要丢失重要内容。\n"
    "- 精简表述，删除无意义的寒暄与冗余解释。\n"
    "- 整理后内容不超过 {max_chars} 个字符。\n"
    "- 只输出整理后的 Markdown 内容本身，不要解释、不要包裹在代码块中。\n\n"
    "记忆全文：\n{content}"
)

# 注入前缀标题
_INJECTION_HEADER = "[长期记忆]"
_USER_SECTION_HEADER = "## 用户档案"
_MEMORY_SECTION_HEADER = "## 记忆"


def build_injection_prefix(user_content: str, memory_content: str) -> str:
    """根据 USER.md 与 MEMORY.md 内容构造会话注入前缀。

    前缀结构：记忆使用引导提示词（MEMORY_GUIDE_PROMPT）+ 标题 + 各文件小节。
    引导提示词始终注入，即使两个文件均为空——LLM 需要知道有记忆工具可用、
    何时该使用它们。当某文件内容为空时省略对应小节，保证前缀简洁可读。

    参数:
        user_content: 用户偏好与环境信息全文。
        memory_content: 项目上下文与学习经验全文。

    返回:
        拼装好的系统提示词前缀文本（至少含记忆使用引导提示词）。
    """
    parts: list[str] = [MEMORY_GUIDE_PROMPT, _INJECTION_HEADER]
    if user_content.strip():
        parts.append(_USER_SECTION_HEADER)
        parts.append(user_content.strip())
    if memory_content.strip():
        parts.append(_MEMORY_SECTION_HEADER)
        parts.append(memory_content.strip())
    return "\n".join(parts)
