from __future__ import annotations

from typing import Any

from schemas import (
    BooksRecommendResult,
    CareerAcademicBlueprint,
    ChapterFrameworkResult,
    ChapterSectionsResult,
    SectionTeachingExpand,
    SkillNode,
    SynthesisMilestone,
    Track,
)


def books_to_markdown(br: BooksRecommendResult) -> str:
    lines: list[str] = ["## 参考书单", ""]
    for i, b in enumerate(br.books, 1):
        lines.append(f"### {i}. 《{b.title}》 — {b.study_role}")
        lines.append(f"- **作者**：{b.authors}")
        if b.edition_or_year_hint:
            lines.append(f"- **版本线索**：{b.edition_or_year_hint}")
        lines.append(f"- **为什么值得读**：{b.why_global_standard}")
        lines.append(f"- **为何适合你**：{b.suitable_for_this_student}")
        lines.append("")
    lines.append("---")
    lines.append(f"*{br.disclaimer}*")
    return "\n".join(lines)


def framework_to_markdown(fr: ChapterFrameworkResult) -> str:
    dl = fr.disciplinary_logic
    lines: list[str] = [
        "## 学习大纲与学法",
        "",
        "### 学科逻辑",
        f"- **核心问题**：{dl.core_question}",
        "",
        "**推理链**：",
    ]
    for i, s in enumerate(dl.reasoning_chain, 1):
        lines.append(f"{i}. {s}")
    if dl.bad_orders:
        lines.extend(["", "**常见错序**：", *[f"- {x}" for x in dl.bad_orders]])
    lines.extend(["", "### 整体学习方法", fr.global_learning_method, "", "## 章列表", ""])
    for ch in fr.chapters:
        lines.append(f"### {ch.chapter_id} · {ch.title}")
        lines.append(f"**核心思想**：{ch.core_ideas}")
        lines.append(f"**本章学法**：{ch.learning_method}")
        lines.append(f"**书目对齐**：{ch.book_reference_note}")
        lines.append("")
        lines.append("**章内目录（细目）**：")
        for t in ch.detailed_toc:
            lines.append(f"- {t}")
        lines.append("")
    return "\n".join(lines)


def chapter_sections_to_markdown(cs: ChapterSectionsResult) -> str:
    lines: list[str] = [f"## 本章小节骨架 · {cs.chapter_id}", ""]
    for sec in cs.sections:
        lines.append(f"### {sec.section_id} · {sec.title}")
        lines.append("**知识点**：")
        for kp in sec.knowledge_points:
            lines.append(f"- {kp}")
        lines.append("")
    return "\n".join(lines)


def section_teaching_to_markdown(sec_title: str, payload: SectionTeachingExpand) -> str:
    t = payload.teaching
    lines: list[str] = [f"## 小节详解 · {sec_title}", "", "### 讲解", t.explain, "", "### 要点"]
    for kp in t.key_points:
        lines.append(f"- {kp}")
    if t.common_pitfalls:
        lines.extend(["", "### 易错点", *[f"- {p}" for p in t.common_pitfalls]])
    if payload.exercises:
        lines.extend(["", "### 练习题"])
        for ex in payload.exercises:
            lines.append(f"- **[{ex.kind}]** {ex.prompt}")
            if ex.hint:
                lines.append(f"  - 提示：{ex.hint}")
            lines.append(f"  - 答案要点：{ex.answer_outline}")
    gp = payload.goal_practice
    if gp:
        lines.extend(
            [
                "",
                "### 实战演练",
                "",
                f"**与目标的关系**：{gp.toward_goal}",
                "",
                f"**情境**：{gp.scenario}",
                "",
                "**步骤**：",
                *[f"{i}. {s}" for i, s in enumerate(gp.steps, start=1)],
                "",
                "**自检**：",
                *[f"- {s}" for s in gp.self_check],
            ]
        )
    return "\n".join(lines)


def build_blueprint_from_pipeline(pipeline: dict[str, Any]) -> CareerAcademicBlueprint | None:
    """把管线状态合成尖塔/节点详情用的蓝图（小节为 SkillNode，章未拆则用占位节点）。"""
    fw_raw = pipeline.get("framework")
    if not fw_raw:
        return None
    fw = ChapterFrameworkResult.model_validate(fw_raw)
    sections_store: dict[str, Any] = pipeline.get("sections") or {}
    teaching_store: dict[str, Any] = pipeline.get("teaching") or {}
    stu = pipeline.get("student") or {}
    meta = dict(fw.meta)
    meta.setdefault("topic", stu.get("topic", ""))
    meta.setdefault("goal", stu.get("goal", ""))
    meta.setdefault("user_context", stu.get("user_context", ""))

    nodes: list[SkillNode] = []
    prev_id: str | None = None
    track = Track(id="t1", name="教材主线")
    for ch in fw.chapters:
        cid = ch.chapter_id
        if cid in sections_store and sections_store[cid]:
            cs = ChapterSectionsResult.model_validate(sections_store[cid])
            for sec in cs.sections:
                kid = sec.section_id
                what = "；".join(sec.knowledge_points)
                t_raw = teaching_store.get(kid)
                teach = None
                exs: list = []
                if t_raw and isinstance(t_raw, dict):
                    try:
                        ste = SectionTeachingExpand.model_validate(t_raw)
                        teach = ste.teaching
                        exs = ste.exercises
                    except Exception:
                        pass
                prereqs = [prev_id] if prev_id else []
                whys = (
                    [f"完成「{prev_id}」后进入本节"] if prev_id else []
                )
                nodes.append(
                    SkillNode(
                        id=kid,
                        track_id=track.id,
                        title=sec.title,
                        what_to_learn=what,
                        how_to_learn=f"参考{ch.title}：{ch.learning_method}",
                        practice=f"对照书目：{ch.book_reference_note[:200]}",
                        prerequisite_ids=prereqs,
                        why_prerequisites=whys,
                        position_in_logic=f"{ch.chapter_id} · {ch.title}",
                        teaching=teach,
                        exercises=exs,
                    )
                )
                prev_id = kid
        else:
            ph_id = f"{cid}_待展开"
            prereqs = [prev_id] if prev_id else []
            whys = [f"依赖「{prev_id}」"] if prev_id else []
            nodes.append(
                SkillNode(
                    id=ph_id,
                    track_id=track.id,
                    title=f"{ch.title}（待生成小节）",
                    what_to_learn=ch.core_ideas[:500],
                    how_to_learn=ch.learning_method,
                    practice="请选好章节后再点「下一步」，生成本章要点",
                    prerequisite_ids=prereqs,
                    why_prerequisites=whys,
                    position_in_logic=f"{ch.chapter_id} · 章占位",
                    teaching=None,
                    exercises=[],
                )
            )
            prev_id = ph_id

    if not nodes:
        return None

    milestones: list[SynthesisMilestone] = []
    for ch in fw.chapters:
        c_nodes = [n for n in nodes if (n.position_in_logic or "").startswith(ch.chapter_id)]
        if not c_nodes:
            continue
        milestones.append(
            SynthesisMilestone(
                title=f"里程碑 · {ch.title}",
                involved_node_ids=[n.id for n in c_nodes][:12],
                deliverables=[f"复盘 {ch.chapter_id} 核心思想并完成本章练习"],
            )
        )

    return CareerAcademicBlueprint(
        meta=meta,
        disciplinary_logic=fw.disciplinary_logic,
        tracks=[track],
        nodes=nodes,
        cross_edges=[],
        synthesis_milestones=milestones or [
            SynthesisMilestone(
                title="合成里程碑",
                involved_node_ids=[nodes[0].id, nodes[-1].id] if len(nodes) > 1 else [nodes[0].id],
                deliverables=["完成主线首节与末节的对照复盘"],
            )
        ],
        interdiscipline_edges=[],
    )


def pipeline_export_markdown(pipeline: dict[str, Any]) -> str:
    parts: list[str] = []
    if pipeline.get("books"):
        parts.append(books_to_markdown(BooksRecommendResult.model_validate(pipeline["books"])))
        parts.append("\n\n")
    if pipeline.get("framework"):
        parts.append(framework_to_markdown(ChapterFrameworkResult.model_validate(pipeline["framework"])))
        parts.append("\n\n")
    for cid, raw in sorted((pipeline.get("sections") or {}).items()):
        parts.append(chapter_sections_to_markdown(ChapterSectionsResult.model_validate(raw)))
        parts.append("\n\n")
    for sid, raw in sorted((pipeline.get("teaching") or {}).items()):
        if isinstance(raw, str):
            parts.append(raw.strip())
        elif isinstance(raw, dict):
            try:
                parts.append(section_teaching_to_markdown(sid, SectionTeachingExpand.model_validate(raw)))
            except Exception:
                parts.append(f"_（{sid} 讲解数据异常）_")
        parts.append("\n\n")
    return "\n".join(parts).strip()
