from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from schemas import CareerAcademicBlueprint, SkillNode


_re_token = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
_re_id_like = re.compile(r"^(ch\\d+|s\\d+|ch\\d+_s\\d+|\\d+)$", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    if not text:
        return set()
    toks = set(m.group(0).lower() for m in _re_token.finditer(text))
    # lightweight stopwords
    stop = {
        "the",
        "and",
        "with",
        "from",
        "this",
        "that",
        "一个",
        "一种",
        "如何",
        "为什么",
        "以及",
        "进行",
        "方法",
        "练习",
        "学习",
        "掌握",
        "理解",
        "知识点",
        "本章",
        "本节",
        # UI / process words that should never be treated as knowledge points
        "主教材范围",
        "主教材",
        "范围",
        "不超纲",
        "先行项",
        "先行",
        "下一步",
        "待展开",
        "必做练习",
        "常见坑",
        "学法",
        "对齐",
    }
    out: set[str] = set()
    for t in toks:
        if len(t) < 2:
            continue
        if t in stop:
            continue
        # drop id-like tokens and pure numbers
        if _re_id_like.match(t):
            continue
        # drop things that are basically chapter/section placeholders
        if "待展开" in t or "下一步" in t:
            continue
        out.add(t)
    return out


def node_keywords(n: SkillNode) -> set[str]:
    return _tokens(" ".join([n.title or "", n.what_to_learn or "", n.how_to_learn or "", n.practice or ""]))


def learned_keywords(item: dict[str, Any]) -> set[str]:
    base = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            " ".join(item.get("keywords") or []),
        ]
    )
    return _tokens(base)


def _overlap_score(a: set[str], b: set[str]) -> int:
    if not a or not b:
        return 0
    return len(a & b)


@dataclass(frozen=True)
class AssocHit:
    kind: str  # internal|learned
    left: str
    right: str
    score: int
    reason: str


def internal_associations(bp: CareerAcademicBlueprint, top_k: int = 12) -> list[AssocHit]:
    # infer similarity links between nodes (excluding direct prerequisites)
    id_to = {n.id: n for n in bp.nodes}
    prereq_pairs = {(p, n.id) for n in bp.nodes for p in (n.prerequisite_ids or [])}
    keys = {n.id: node_keywords(n) for n in bp.nodes}
    hits: list[AssocHit] = []
    # skip placeholder nodes (e.g., “待展开”)
    ids = [n.id for n in bp.nodes if "待展开" not in (n.title or "") and "待展开" not in (n.id or "")]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if a == b:
                continue
            if (a, b) in prereq_pairs or (b, a) in prereq_pairs:
                continue
            sc = _overlap_score(keys[a], keys[b])
            if sc <= 1:
                continue
            shared = sorted(list(keys[a] & keys[b]))[:6]
            if not shared:
                continue
            hits.append(
                AssocHit(
                    kind="internal",
                    left=a,
                    right=b,
                    score=sc,
                    reason="共同关键词：" + "、".join(shared),
                )
            )
    # dedupe by unordered pair
    best: dict[tuple[str, str], AssocHit] = {}
    for h in hits:
        a, b = sorted([h.left, h.right])
        k = (a, b)
        if k not in best or h.score > best[k].score:
            best[k] = h
    out = sorted(best.values(), key=lambda x: x.score, reverse=True)
    return out[: max(top_k, 0)]


def learned_associations(
    *,
    bp: CareerAcademicBlueprint,
    learned_items: list[dict[str, Any]],
    current_discipline: str,
    top_k: int = 12,
) -> list[AssocHit]:
    # match learned items to current nodes
    learned = []
    for it in learned_items:
        disc = str(it.get("discipline") or "")
        if disc and disc == current_discipline:
            # still allowed, but treat as learned-in-discipline
            pass
        kw = learned_keywords(it)
        if not kw:
            continue
        learned.append((it, kw))

    if not learned:
        return []

    hits: list[AssocHit] = []
    for n in bp.nodes:
        if "待展开" in (n.title or "") or "待展开" in (n.id or ""):
            continue
        nkw = node_keywords(n)
        if not nkw:
            continue
        for it, kw in learned:
            ref = str(it.get("source_ref") or "")
            if ref.endswith(f":{n.id}"):
                continue
            sc = _overlap_score(nkw, kw)
            if sc <= 1:
                continue
            shared = sorted(list(nkw & kw))[:6]
            if not shared:
                continue
            hits.append(
                AssocHit(
                    kind="learned",
                    left=n.id,
                    right=str(it.get("title") or "已学知识"),
                    score=sc,
                    reason="共同关键词：" + "、".join(shared),
                )
            )
    # dedupe by (node,title)
    best: dict[tuple[str, str], AssocHit] = {}
    for h in hits:
        k = (h.left, h.right)
        if k not in best or h.score > best[k].score:
            best[k] = h
    out = sorted(best.values(), key=lambda x: x.score, reverse=True)
    return out[: max(top_k, 0)]


def render_associations_markdown(
    *,
    bp: CareerAcademicBlueprint,
    learned_items: list[dict[str, Any]],
    discipline: str,
) -> str:
    lines: list[str] = []
    lines.append("## 当前学科内关联")
    ins = internal_associations(bp)
    if not ins:
        lines.append("_（暂未发现明显可并联/类比的节点，主要按前置链推进即可）_")
    else:
        for h in ins:
            lines.append(f"- **{h.left}** ↔ **{h.right}**：{h.reason}")

    lines.append("")
    lines.append("## 与已学知识点的关联")
    las = learned_associations(bp=bp, learned_items=learned_items, current_discipline=discipline)
    if not learned_items:
        lines.append(
            "_（你还没有记录“已学知识点”。请在 **小节详解** 底部点 **「已学会」**，"
            "再在「节点关联」点 **「刷新已学库」** 同步下拉菜单。）_"
        )
    elif not las:
        lines.append("_（未发现高置信匹配：可能是关键词不足或学科跨度较大）_")
    else:
        for h in las:
            lines.append(f"- **{h.left}** ↔ **{h.right}**：{h.reason}")

    return "\n".join(lines).strip()

