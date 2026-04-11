from __future__ import annotations


SYSTEM = """\
你是一名善于讲解的助教。你必须输出严格 JSON（不要任何多余文字）。

目标：给指定知识点写清晰讲解 + 要点 + 易错点 + 练习题（含答案要点）。
要求：
- explain 必须连贯、分段清晰（可用换行），避免堆概念名。
- key_points 至少 3 条，尽量可背诵。
- exercises 至少 1 道，必须与该节点强相关；给 hint（可选）与 answer_outline（要点级，不要长篇推导）。
"""


def user_prompt(*, topic: str, node_title: str, node_what: str, node_how: str) -> str:
    return f"""\
主题：{topic}
知识点：{node_title}
该节点“学什么”：{node_what}
该节点“怎么学”：{node_how}

输出 JSON 字段：
{{
  "teaching": {{ "explain": str, "key_points": string[], "common_pitfalls": string[] }},
  "exercises": [{{ "id": str, "prompt": str, "kind": "concept|calculation|short_answer|design", "hint": str|null, "answer_outline": str }}]
}}
"""

