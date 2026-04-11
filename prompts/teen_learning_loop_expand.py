from __future__ import annotations

SYSTEM = """\
你是"分段扩写器"，负责把学习闭环中的**某一个区块**扩写得更扎实、更好学。

硬性约束（必须全部满足）：
- 只输出该区块的**正文内容**（不要重复区块标题、不要输出分隔线、不要加新小标题、不要输出提示符）。
- 只基于用户提供的材料与 seed 文本进行扩写；不要引用/编造外部论文、统计数据、学校机构、网址链接等。
- 语言要面向国内大学生：生活类比要具体；专业术语少且出现时先用白话解释。
- 一次只讲一个核心概念；不要在本段引入与本段无关的新概念。
- 目标长度：**300～500 字（中文计数，去掉空白后）**。

排版规则（必须遵守）：
- **禁止一整段不分段的大块文字**。每段 3～5 行（约 60～100 字）后换行空一行再写下一段。
- 善用 **加粗** 突出关键词、要点。
- 当内容涉及并列/对比时，用 Markdown 列表（`-` 或 `1.`）。
- 像"网文"一样排版：短段落、留白多、阅读节奏快。\
"""


def user_prompt(
    *,
    block_no: str,
    block_title: str,
    seed_text: str,
    topic: str,
    goal: str,
    user_context: str,
    chapter_title: str,
    section_title: str,
    knowledge_points_lines: str,
    chapter_core: str,
    extra_require: str,
) -> str:
    ctx = (user_context or "").strip() or "（未提供）"
    kpl = (knowledge_points_lines or "").strip() or "（见本节标题）"
    core = (chapter_core or "").strip() or "（无）"
    extra = (extra_require or "").strip() or "（无）"
    seed = (seed_text or "").strip() or "（空）"
    return f"""\
你正在扩写的区块：{block_title}（编号 {block_no}）

【材料（必须以此为准）】
- 学科/主题：{topic}
- 学生目标：{goal}
- 学生背景与约束：{ctx}
- 所属章：{chapter_title}
- 本节标题：{section_title}
- 本节要点清单：
{kpl}
- 本章核心思想（对齐深度）：{core}

【seed（上一轮模板/草稿）】
{seed}

【本轮额外要求（来自校验反馈）】
{extra}

请把该区块扩写为 300～500 字的正文，直接输出正文即可。注意排版：短段落、留白、善用加粗和列表。\
"""
