from __future__ import annotations


SYSTEM = """\
你是一名熟悉本科教学与校招的助教。你必须输出严格 JSON（不要任何多余文字）。禁止只罗列章节标题。

硬性要求：
1) 输出 disciplinary_logic：core_question / reasoning_chain / bad_orders。
2) 输出 nodes 与 prerequisite_ids，并且每条依赖必须能解释清楚“为什么先学”（机制级 why）。
3) cross_edges（跨轨/跨模块）也必须给出机制级 why。
4) 不要输出无意义空话；用清晰、可验证的因果描述。

注意：
- id 必须是短字符串（如 n1, n2...；track 用 t1,t2...）。
- prerequisite_ids 引用必须存在于 nodes.id。
- reasoning_chain 请用有序、短句。
"""


def user_prompt(*, topic: str, user_context: str, goal: str) -> str:
    return f"""\
请为以下学习目标生成“学科逻辑路径蓝图”。

主题/技能：{topic}
用户背景：{user_context}
目标：{goal}

输出 JSON 字段：
- meta: {{ "topic": str, "goal": str, "user_context": str }}
- disciplinary_logic: {{ core_question: str, reasoning_chain: string[], bad_orders: string[] }}
- tracks: [{{ id, name }}]（至少 1 条）
- nodes: [SkillNode...]（至少 8 个节点，覆盖从入门到综合；每个节点包含 what_to_learn/how_to_learn/practice/prerequisite_ids/why_prerequisites/position_in_logic）
- cross_edges: [{{ from_node_id, to_node_id, relation, why }}]（可为空；relation 仅可为 requires/enriches/can_parallel_if）
- synthesis_milestones: [{{ title, involved_node_ids, deliverables }}]（可为空，但职业向建议给 1-2 个）
- interdiscipline_edges: []（先留空，跨学科后续模块再做）

约束：
- why_prerequisites 与 prerequisite_ids 一一对应（长度相同），每条 why 必须解释“先学前置”的机制。
- 不要引用不存在的 id。
"""

