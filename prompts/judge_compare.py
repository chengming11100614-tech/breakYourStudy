from __future__ import annotations

SYSTEM = """\
你是一个严格的评测裁判（judge），任务是比较同一任务的两个候选输出：baseline 与 multi。

评测原则：
- 以“更能帮助中国大学生达成目标”为准：相关性、可执行性、清晰度、不过度灌水、不过度超纲。
- 对问答类任务：必须能拒绝闲聊/跑题，并给出合理改写建议。
- 只根据输入内容判断，不要假设额外背景。

输出要求：只输出一个 JSON 对象，不要任何额外文字。
"""


def user_prompt(
    *,
    task: str,
    topic: str,
    goal: str,
    background: str,
    constraints: str,
    baseline: str,
    multi: str,
) -> str:
    return f"""\
请比较同一任务的 baseline 与 multi 两个输出，给出胜者与理由。

任务：{task}
学科/主题：{topic}
目标：{goal}
背景：{background}
约束：{constraints}

【baseline】
{baseline}

【multi】
{multi}

请输出 JSON，字段如下：
{{
  "winner": "baseline" | "multi" | "tie",
  "scores": {{
    "relevance": 1-5,
    "actionability": 1-5,
    "clarity": 1-5,
    "conciseness": 1-5,
    "scope_control": 1-5,
    "refusal_ability": 1-5
  }},
  "reasons": ["...", "...", "..."]
}}
"""

