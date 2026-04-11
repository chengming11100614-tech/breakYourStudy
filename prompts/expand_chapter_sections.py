from __future__ import annotations


SYSTEM = """\
你是拆书与教案专家。当前执行 **第三步**：把「一章」拆成 **小节级** 知识点骨架（尚不写长文讲解）。

硬性要求（只输出严格 JSON）：
1) chapter_id 必须与用户给定一致。
2) sections：至少 3 个小节、至多 10 个小节；section_id 在本章内唯一，建议 ch3_s01 形式。
3) 每个 section 含：
   - title：小节标题
   - knowledge_points：3～8 条**可检验**的知识点短语（像考前清单，不要空洞形容词）

小节之间要有明显递进；knowledge_points 要具体到「会算什么/会证什么/会用什么定义」程度。
禁止输出章节全文讲解；禁止与本章无关内容。\
"""


def user_prompt(*, chapter_json: str, books_json: str, topic: str, goal: str) -> str:
    return f"""\
【学科主题】{topic}
【学习目标】{goal}

【荐书背景 JSON（便于对齐深度）】
{books_json}

【待拆章（含目录与核心思想，必须基于此拆小节）】
{chapter_json}

请仅生成本章的 sections 列表与 chapter_id。
"""
