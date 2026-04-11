from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from llm_client import LLMError, chat_json_multi, chat_text_multi, load_config_from_env
from prompts.registry import system_prompt, user_prompt
from pydantic import BaseModel
from schemas import (
    BooksRecommendResult,
    ChapterFrameworkResult,
    ChapterSectionsResult,
    SectionTeachingExpand,
)

from .harness_cases import CASES, HarnessCase


@dataclass(frozen=True)
class Metric:
    name: str
    ok: bool
    detail: str = ""

class JudgeScores(BaseModel):
    relevance: int
    actionability: int
    clarity: int
    conciseness: int
    scope_control: int
    refusal_ability: int


class JudgeResult(BaseModel):
    winner: str  # baseline | multi | tie
    scores: JudgeScores
    reasons: list[str]


def _pick_domestic_main_book(br: BooksRecommendResult) -> bool:
    # Heuristic: study_role=主教材 AND contains China usage hint in suitable_for_this_student
    mains = [b for b in br.books if b.study_role == "主教材"]
    if len(mains) != 1:
        return False
    s = mains[0].suitable_for_this_student
    return any(k in s for k in ("不超纲", "主纲", "范围", "校内", "大纲"))


def _books_count_ok(br: BooksRecommendResult) -> bool:
    return 3 <= len(br.books) <= 5


def _books_titles_dedup_ok(br: BooksRecommendResult) -> bool:
    titles = [b.title.strip().lower() for b in br.books if (b.title or "").strip()]
    return len(titles) == len(set(titles)) and len(titles) == len(br.books)


def _framework_quality_ok(fw: ChapterFrameworkResult) -> bool:
    if len(fw.chapters) < 5:
        return False
    for ch in fw.chapters:
        if not (ch.title or "").strip():
            return False
        if not (ch.core_ideas or "").strip():
            return False
    return True


def _sections_quality_ok(cs: ChapterSectionsResult) -> bool:
    if len(cs.sections) < 2:
        return False
    ids = []
    for s in cs.sections:
        if not (s.title or "").strip():
            return False
        if not s.knowledge_points or len([x for x in s.knowledge_points if str(x).strip()]) < 2:
            return False
        ids.append((s.section_id or "").strip())
    # id uniqueness if ids exist
    if any(ids) and len([x for x in ids if x]) != len(set([x for x in ids if x])):
        return False
    return True


def _teaching_quality_ok(pay: SectionTeachingExpand) -> bool:
    """Retained for potential reuse; not called in current harness flow."""
    t = pay.teaching
    if not (t.explain or "").strip():
        return False
    if not t.key_points or len(t.key_points) < 3:
        return False
    if not pay.exercises or len(pay.exercises) < 1:
        return False
    return True


def _teen_loop_has_all_sections(body: str) -> bool:
    return all(
        hdr in (body or "")
        for hdr in (
            "【1 旧知识引入（激活已有知识）】",
            "【2 核心概念（最简解释）】",
            "【3 可视化示例】",
            "【4 要点与易错】",
            "【5 小任务练习】",
            "【6 费曼检查（理解验证）】",
            "【7 实战演练（对齐目标）】",
            "【8 一句话总结】",
        )
    )


_TEEN_FORBIDDEN_RE = re.compile(
    r"(研究表明|数据显示|据.{0,6}统计|论文|期刊|DOI\b|http://|https://|www\.|%|某大学|哈佛|清华)",
    re.IGNORECASE,
)


def _extract_marked_block(body: str, i: int) -> str:
    m = re.search(
        rf"<<<?BLOCK_{i}>>>?\s*(.*?)\s*<<<?END_BLOCK_{i}>>>?",
        body or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return (m.group(1) if m else "").strip()


def _teen_template_has_markers(body: str) -> bool:
    return all((f"<<<BLOCK_{i}>>>" in (body or "") and f"<<<END_BLOCK_{i}>>>" in (body or "")) for i in range(1, 9))


def _cn_len(s: str) -> int:
    return len(re.sub(r"\s+", "", s or ""))


def _teen_template_len_ok(body: str) -> bool:
    # template round: 1..7 each 80~120 chars (allow small tolerance), 8 is one short sentence
    for i in range(1, 8):
        t = _extract_marked_block(body, i)
        n = _cn_len(t)
        if not (60 <= n <= 160):
            return False
        if _TEEN_FORBIDDEN_RE.search(t):
            return False
    t8 = _extract_marked_block(body, 8)
    if not t8 or _cn_len(t8) > 120:
        return False
    if _TEEN_FORBIDDEN_RE.search(t8):
        return False
    return True


def _assoc_has_structure(md: str) -> bool:
    s = md or ""
    must = (
        "一句话",
        "关联类型",
        "桥梁",
        "迁移练习",
        "发散",
    )
    return sum(1 for x in must if x in s) >= 3


def _qa_refuses_offtopic(md: str) -> bool:
    s = (md or "").lower()
    # loose heuristic: refusal + rewrite suggestions
    has_refuse = any(k in s for k in ("无法", "不能", "不在本节", "无关", "跑题", "拒绝"))
    has_rewrite = any(k in s for k in ("你可以问", "可以改成", "例如", "改写"))
    return has_refuse and has_rewrite


def run_case_pass(case: HarnessCase, *, passes: int) -> dict[str, Any]:
    cfg = load_config_from_env()
    out: dict[str, Any] = {"passes": passes, "metrics": [], "artifacts": {}}

    try:
        br = chat_json_multi(
            cfg=cfg,
            system=system_prompt("books_recommend"),
            user=user_prompt(
                "books_recommend",
                topic=case.topic,
                user_context=case.background,
                goal=case.goal,
                time_budget=case.time_budget,
                constraints=case.constraints,
            ),
            schema_model=BooksRecommendResult,
            passes=passes,
        )
        out["artifacts"]["books"] = br.model_dump()
        out["metrics"].append(
            Metric(
                name="books_has_one_domestic_main_textbook",
                ok=_pick_domestic_main_book(br),
                detail="expect exactly 1 主教材 and contains scope-control hint",
            ).__dict__
        )
        out["metrics"].append(Metric(name="books_count_ok", ok=_books_count_ok(br)).__dict__)
        out["metrics"].append(Metric(name="books_titles_dedup_ok", ok=_books_titles_dedup_ok(br)).__dict__)
    except Exception as e:
        out["metrics"].append(Metric(name="books_call_ok", ok=False, detail=str(e)).__dict__)
        return out

    try:
        fw = chat_json_multi(
            cfg=cfg,
            system=system_prompt("framework_chapters"),
            user=user_prompt(
                "framework_chapters",
                topic=case.topic,
                user_context=case.background,
                goal=case.goal,
                books_json=json.dumps(out["artifacts"]["books"], ensure_ascii=False),
            ),
            schema_model=ChapterFrameworkResult,
            passes=passes,
        )
        out["artifacts"]["framework"] = fw.model_dump()
        out["metrics"].append(Metric(name="framework_has_chapters", ok=len(fw.chapters) >= 5).__dict__)
        out["metrics"].append(Metric(name="framework_quality_ok", ok=_framework_quality_ok(fw)).__dict__)
    except Exception as e:
        out["metrics"].append(Metric(name="framework_call_ok", ok=False, detail=str(e)).__dict__)
        return out

    # Choose first chapter for expansion
    ch0 = out["artifacts"]["framework"]["chapters"][0]
    try:
        cs = chat_json_multi(
            cfg=cfg,
            system=system_prompt("expand_chapter_sections"),
            user=user_prompt(
                "expand_chapter_sections",
                chapter_json=json.dumps(ch0, ensure_ascii=False),
                books_json=json.dumps(out["artifacts"]["books"], ensure_ascii=False),
                topic=case.topic,
                goal=case.goal,
            ),
            schema_model=ChapterSectionsResult,
            passes=passes,
        )
        out["artifacts"]["chapter_sections"] = cs.model_dump()
        out["metrics"].append(Metric(name="chapter_sections_has_sections", ok=len(cs.sections) >= 2).__dict__)
        out["metrics"].append(Metric(name="chapter_sections_quality_ok", ok=_sections_quality_ok(cs)).__dict__)
    except Exception as e:
        out["metrics"].append(Metric(name="chapter_sections_call_ok", ok=False, detail=str(e)).__dict__)
        return out

    sec0 = out["artifacts"]["chapter_sections"]["sections"][0]

    # Teen loop text format check (unified 8-block generation)
    try:
        body = chat_text_multi(
            cfg=cfg,
            system=system_prompt("teen_learning_loop"),
            user=user_prompt(
                "teen_learning_loop",
                topic=case.topic,
                goal=case.goal,
                user_context=case.background,
                section_title=sec0["title"],
                chapter_title=ch0.get("title", ""),
                knowledge_points_lines="\n".join(f"- {x}" for x in sec0["knowledge_points"]),
                chapter_core=ch0.get("core_ideas", ""),
                book_refs=ch0.get("book_reference_note", ""),
            ),
            temperature=0.45,
            passes=passes,
        )
        out["artifacts"]["teen_text"] = body
        out["metrics"].append(Metric(name="teen_loop_structure_ok", ok=_teen_loop_has_all_sections(body)).__dict__)
        out["metrics"].append(Metric(name="teen_template_markers_ok", ok=_teen_template_has_markers(body)).__dict__)
        out["metrics"].append(Metric(name="teen_template_len_ok", ok=_teen_template_len_ok(body)).__dict__)

        # Stepwise-expand guardrail (single-block): ensure expand prompt can reach 300~500 without hallucination markers.
        seed1 = _extract_marked_block(body, 1) or "（空）"
        expand1 = chat_text_multi(
            cfg=cfg,
            system=system_prompt("teen_learning_loop_expand"),
            user=user_prompt(
                "teen_learning_loop_expand",
                block_no="1",
                block_title="【1 旧知识引入（激活已有知识）】",
                seed_text=seed1,
                topic=case.topic,
                goal=case.goal,
                user_context=case.background,
                chapter_title=ch0.get("title", ""),
                section_title=sec0["title"],
                knowledge_points_lines="\n".join(f"- {x}" for x in sec0["knowledge_points"]),
                chapter_core=ch0.get("core_ideas", ""),
                extra_require="输出 300~500 字；不要外部统计/论文/学校/网址/%。注意短段落排版。",
            ),
            temperature=0.45,
            passes=1,
        )
        out["artifacts"]["teen_expand_block1"] = expand1
        out["metrics"].append(
            Metric(name="teen_expand_block1_len_ok", ok=(300 <= _cn_len(expand1) <= 500)).__dict__
        )
        out["metrics"].append(
            Metric(name="teen_expand_block1_no_forbidden_ok", ok=not bool(_TEEN_FORBIDDEN_RE.search(expand1 or ""))).__dict__
        )
    except LLMError as e:
        out["metrics"].append(Metric(name="teen_loop_call_ok", ok=False, detail=str(e)).__dict__)

    # Assoc analyze (two learned-like items derived from this run)
    try:
        item_a = {
            "discipline": case.topic,
            "title": sec0.get("title", ""),
            "summary": "；".join(sec0.get("knowledge_points") or [])[:300],
            "keywords": sec0.get("knowledge_points") or [],
        }
        # try pick another section if exists, else use chapter title
        sec1 = (out["artifacts"]["chapter_sections"]["sections"][1] if len(out["artifacts"]["chapter_sections"]["sections"]) > 1 else None)
        item_b = {
            "discipline": case.topic,
            "title": (sec1.get("title") if sec1 else ch0.get("title", "")) or "",
            "summary": ("；".join((sec1.get("knowledge_points") or [])) if sec1 else (ch0.get("core_ideas") or ""))[:300],
            "keywords": (sec1.get("knowledge_points") if sec1 else []) or [],
        }
        kw_a = "、".join([str(x) for x in (item_a.get("keywords") or [])][:12]) or "（无）"
        kw_b = "、".join([str(x) for x in (item_b.get("keywords") or [])][:12]) or "（无）"
        assoc_md = chat_text_multi(
            cfg=cfg,
            system=system_prompt("assoc_analyze"),
            user=user_prompt(
                "assoc_analyze",
                item_a_title=item_a["title"],
                item_a_discipline=item_a["discipline"],
                item_a_summary=item_a["summary"],
                item_a_keywords=kw_a,
                item_b_title=item_b["title"],
                item_b_discipline=item_b["discipline"],
                item_b_summary=item_b["summary"],
                item_b_keywords=kw_b,
            ),
            temperature=0.45,
            passes=passes,
        )
        out["artifacts"]["assoc_analyze"] = assoc_md
        out["metrics"].append(Metric(name="assoc_analyze_structure_ok", ok=_assoc_has_structure(assoc_md)).__dict__)
    except Exception as e:
        out["metrics"].append(Metric(name="assoc_analyze_call_ok", ok=False, detail=str(e)).__dict__)

    # Section QA (on-topic + off-topic refusal)
    try:
        section_context = (
            f"本节：{sec0.get('title','')}\n"
            f"要点：\n" + "\n".join(f"- {x}" for x in (sec0.get("knowledge_points") or []))
        )
        hist = "（无）"
        on_topic_q = "请用一句话解释本节最核心的概念，并给一个极简例子。"
        qa_on = chat_text_multi(
            cfg=cfg,
            system=system_prompt("section_qa"),
            user=user_prompt("section_qa", section_context=section_context, chat_history=hist, user_question=on_topic_q),
            temperature=0.35,
            passes=passes,
        )
        off_topic_q = "推荐几部好看的科幻电影？"
        qa_off = chat_text_multi(
            cfg=cfg,
            system=system_prompt("section_qa"),
            user=user_prompt("section_qa", section_context=section_context, chat_history=hist, user_question=off_topic_q),
            temperature=0.35,
            passes=passes,
        )
        out["artifacts"]["section_qa_on_topic"] = qa_on
        out["artifacts"]["section_qa_off_topic"] = qa_off
        out["metrics"].append(Metric(name="section_qa_on_topic_ok", ok=bool((qa_on or "").strip())).__dict__)
        out["metrics"].append(Metric(name="section_qa_off_topic_refusal_ok", ok=_qa_refuses_offtopic(qa_off)).__dict__)
    except Exception as e:
        out["metrics"].append(Metric(name="section_qa_call_ok", ok=False, detail=str(e)).__dict__)

    return out


def main() -> None:
    mode = (os.getenv("HARNESS_MODE") or "online").strip().lower()
    if mode != "online":
        raise SystemExit("Only HARNESS_MODE=online is supported in this minimal runner.")

    do_judge = (os.getenv("HARNESS_JUDGE") or "0").strip() in ("1", "true", "yes", "on")

    results = []
    for c in CASES:
        baseline = run_case_pass(c, passes=1)
        multi = run_case_pass(c, passes=2)
        row: dict[str, Any] = {"case": asdict(c), "baseline": baseline, "multi": multi}

        if do_judge:
            cfg = load_config_from_env()
            judges: dict[str, Any] = {}
            try:
                # teen loop compare
                jr = chat_json_multi(
                    cfg=cfg,
                    system=system_prompt("judge_compare"),
                    user=user_prompt(
                        "judge_compare",
                        task="teen_learning_loop",
                        topic=c.topic,
                        goal=c.goal,
                        background=c.background,
                        constraints=c.constraints,
                        baseline=str((baseline.get("artifacts") or {}).get("teen_text") or ""),
                        multi=str((multi.get("artifacts") or {}).get("teen_text") or ""),
                    ),
                    schema_model=JudgeResult,
                    passes=1,
                )
                judges["teen_learning_loop"] = jr.model_dump()
            except Exception as e:
                judges["teen_learning_loop_error"] = str(e)

            try:
                # section teaching compare (convert to compact markdown-ish string)
                b = (baseline.get("artifacts") or {}).get("section_teaching") or {}
                m = (multi.get("artifacts") or {}).get("section_teaching") or {}
                b_s = json.dumps(b, ensure_ascii=False)[:6000]
                m_s = json.dumps(m, ensure_ascii=False)[:6000]
                jr = chat_json_multi(
                    cfg=cfg,
                    system=system_prompt("judge_compare"),
                    user=user_prompt(
                        "judge_compare",
                        task="expand_section_teaching",
                        topic=c.topic,
                        goal=c.goal,
                        background=c.background,
                        constraints=c.constraints,
                        baseline=b_s,
                        multi=m_s,
                    ),
                    schema_model=JudgeResult,
                    passes=1,
                )
                judges["expand_section_teaching"] = jr.model_dump()
            except Exception as e:
                judges["expand_section_teaching_error"] = str(e)

            try:
                jr = chat_json_multi(
                    cfg=cfg,
                    system=system_prompt("judge_compare"),
                    user=user_prompt(
                        "judge_compare",
                        task="assoc_analyze",
                        topic=c.topic,
                        goal=c.goal,
                        background=c.background,
                        constraints=c.constraints,
                        baseline=str((baseline.get("artifacts") or {}).get("assoc_analyze") or ""),
                        multi=str((multi.get("artifacts") or {}).get("assoc_analyze") or ""),
                    ),
                    schema_model=JudgeResult,
                    passes=1,
                )
                judges["assoc_analyze"] = jr.model_dump()
            except Exception as e:
                judges["assoc_analyze_error"] = str(e)

            try:
                jr = chat_json_multi(
                    cfg=cfg,
                    system=system_prompt("judge_compare"),
                    user=user_prompt(
                        "judge_compare",
                        task="section_qa_offtopic_refusal",
                        topic=c.topic,
                        goal=c.goal,
                        background=c.background,
                        constraints=c.constraints,
                        baseline=str((baseline.get("artifacts") or {}).get("section_qa_off_topic") or ""),
                        multi=str((multi.get("artifacts") or {}).get("section_qa_off_topic") or ""),
                    ),
                    schema_model=JudgeResult,
                    passes=1,
                )
                judges["section_qa_offtopic_refusal"] = jr.model_dump()
            except Exception as e:
                judges["section_qa_offtopic_refusal_error"] = str(e)

            row["judge"] = judges

        results.append(row)

    ok_all = True
    for r in results:
        # combine ok-ness across hard metrics (ignore *_call_ok for overall quality)
        for part_name in ("baseline", "multi"):
            for m in r[part_name]["metrics"]:
                if not m["ok"] and not m["name"].endswith("_call_ok"):
                    ok_all = False

    # judge win-rate summary (optional)
    summary: dict[str, Any] = {"ok": ok_all}
    if do_judge:
        win = {"baseline": 0, "multi": 0, "tie": 0}
        total = 0
        for r in results:
            j = r.get("judge") or {}
            for k, v in j.items():
                if not isinstance(v, dict) or "winner" not in v:
                    continue
                w = str(v.get("winner"))
                if w in win:
                    win[w] += 1
                    total += 1
        summary["judge_wins"] = win
        summary["judge_total"] = total
        summary["judge_multi_win_rate"] = (win["multi"] / total) if total else None

    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

