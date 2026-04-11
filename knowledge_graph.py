from __future__ import annotations

import html
import json
import math
from typing import Any

from storage import load_assoc_edges, load_learned, list_projects, load_project


def learned_node_id(item: dict[str, Any]) -> str:
    """Stable id for graph endpoints: prefer source_ref, else (discipline, title, source_ref) tuple key."""
    ref = str(item.get("source_ref") or "").strip()
    disc = str(item.get("discipline") or "").strip()
    title = str(item.get("title") or "").strip()
    if ref:
        return "ref:" + ref
    return "dt:" + json.dumps([disc, title, ref], ensure_ascii=False, separators=(",", ":"))


def title_short_label(title: str, n: int = 3) -> str:
    t = (title or "").strip()
    if not t:
        return "·"
    out: list[str] = []
    for ch in t:
        if ch.isspace():
            continue
        if ch in "，。、；：「」''""（）【】《》·…—-":
            continue
        out.append(ch)
        if len(out) >= n:
            break
    return "".join(out) if out else t[:n]


def _escape_svg_text(s: str) -> str:
    return html.escape(s or "", quote=False)


def _learned_index_by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        out[learned_node_id(it)] = it
    return out


def build_knowledge_network_html(*, max_nodes: int = 40, max_edges: int = 60) -> str:
    edges_store = load_assoc_edges()
    raw_edges = list(edges_store.get("edges") or [])
    if not raw_edges:
        return (
            "<div class='forest-card' style='padding:16px;'>"
            "<p>还没有持久化的关联边。请到项目工作台 → <strong>节点关联</strong> → "
            "「分析两者关联」建立连接后再点「刷新网络」。</p></div>"
        )

    learned = load_learned()
    by_id = _learned_index_by_id(learned)

    node_ids: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for e in raw_edges[:max_edges]:
        a = str(e.get("a_id") or "").strip()
        b = str(e.get("b_id") or "").strip()
        if not a or not b or a == b:
            continue
        node_ids.add(a)
        node_ids.add(b)
        pairs.append((a, b))

    if not node_ids:
        return (
            "<div class='forest-card' style='padding:16px;'>"
            "<p>边表为空或无效。请重新建立关联。</p></div>"
        )

    ids = sorted(node_ids)[:max_nodes]
    id_set = set(ids)
    pairs = [(a, b) for a, b in pairs if a in id_set and b in id_set]

    n = len(ids)
    cx, cy, r = 260, 200, 150
    positions: dict[str, tuple[float, float]] = {}
    for i, nid in enumerate(ids):
        ang = 2 * math.pi * i / max(n, 1) - math.pi / 2
        positions[nid] = (cx + r * math.cos(ang), cy + r * math.sin(ang))

    def node_title(nid: str) -> str:
        it = by_id.get(nid)
        if not it:
            return nid
        disc = str(it.get("discipline") or "").strip()
        tit = str(it.get("title") or "").strip()
        if disc and tit:
            return f"{disc} · {tit}"
        return tit or nid

    svg_parts: list[str] = []
    svg_parts.append(
        f"<svg viewBox='0 0 520 400' width='100%' height='400' xmlns='http://www.w3.org/2000/svg' "
        "style='background:#FAFAF8;border-radius:12px;'>"
    )
    for a, b in pairs:
        xa, ya = positions.get(a, (cx, cy))
        xb, yb = positions.get(b, (cx, cy))
        svg_parts.append(
            f"<line x1='{xa:.1f}' y1='{ya:.1f}' x2='{xb:.1f}' y2='{yb:.1f}' "
            "stroke='#A8C9A4' stroke-width='2' opacity='0.85'/>"
        )

    for nid in ids:
        x, y = positions[nid]
        tit = node_title(nid)
        short = title_short_label(tit, 3)
        g_open = f"<g transform='translate({x:.1f},{y:.1f})'>"
        title_el = f"<title>{_escape_svg_text(tit)}</title>"
        circle = (
            f"<circle r='28' fill='#7BAE7F' stroke='#5E8B62' stroke-width='2'/>"
            f"<text text-anchor='middle' dy='5' fill='white' font-size='14' font-weight='700' "
            f">{_escape_svg_text(short)}</text>"
        )
        svg_parts.append(g_open + title_el + circle + "</g>")

    svg_parts.append("</svg>")

    in_graph = set(ids)
    for a, b in pairs:
        in_graph.add(a)
        in_graph.add(b)
    all_endpoints: set[str] = set()
    for e in raw_edges:
        aa = str(e.get("a_id") or "").strip()
        bb = str(e.get("b_id") or "").strip()
        if aa:
            all_endpoints.add(aa)
        if bb:
            all_endpoints.add(bb)

    unlinked = 0
    for it in learned:
        nid = learned_node_id(it)
        if nid not in all_endpoints:
            unlinked += 1

    corner = ""
    if unlinked > 0:
        corner = (
            f"<div style='position:absolute;right:8px;bottom:8px;font-size:12px;color:#5a6b5a;"
            f"background:rgba(255,255,255,0.92);padding:6px 10px;border-radius:8px;"
            f"box-shadow:0 1px 4px rgba(0,0,0,0.06);max-width:70%;text-align:right;'>"
            f"另有 <strong>{unlinked}</strong> 个知识点尚未建立关联"
            "</div>"
        )
    else:
        corner = (
            "<div style='position:absolute;right:8px;bottom:8px;font-size:11px;color:#7B7B7B;"
            "background:rgba(255,255,255,0.85);padding:4px 8px;border-radius:6px;'>"
            "已全部建立关联"
            "</div>"
        )

    wrap = (
        "<div style='position:relative;display:inline-block;width:100%;'>"
        + "".join(svg_parts)
        + corner
        + "</div>"
    )
    return wrap


def _truncate(s: str, n: int) -> str:
    t = (s or "").strip().replace("\n", " ")
    return t if len(t) <= n else t[: n - 1] + "…"


def build_rule_profile_markdown() -> str:
    """Rule-based capability / study profile from all projects + learned stats (no LLM)."""
    items = list_projects()
    learned = load_learned()
    lines: list[str] = [
        "## 能力与学习概览（规则摘要）",
        "",
        "_以下统计由本地已保存的项目与「已学库」汇总生成，**非标准化测评**，仅供自我回顾。_",
        "",
        f"- **已学库条目**：{len(learned)} 条",
    ]

    by_disc: dict[str, int] = {}
    for it in learned:
        d = str(it.get("discipline") or "（未填学科）").strip() or "（未填学科）"
        by_disc[d] = by_disc.get(d, 0) + 1
    if by_disc:
        top = sorted(by_disc.items(), key=lambda x: -x[1])[:8]
        lines.append(f"- **学科分布**：{', '.join(f'{k}（{v}）' for k, v in top)}")

    lines.extend(["", "### 多项目合并画像", ""])

    if not items:
        lines.append("_暂无本地项目。先在「开始学习」里创建并保存项目。_")
        return "\n".join(lines)

    lines.append(f"_共读取 **{len(items)}** 个本地项目（以下按列表顺序展示）。_")
    lines.append("")

    for meta in items:
        raw = load_project(meta.project_id)
        pl = (raw or {}).get("pipeline") or {}
        st = pl.get("student") or {}
        topic = str(st.get("topic") or "").strip() or "（无主题）"
        goal = str(st.get("goal") or "").strip()
        uc = _truncate(str(st.get("user_context") or ""), 240)
        lines.append(f"#### {meta.title or topic}")
        lines.append(f"- **项目 id**：`{meta.project_id}`")
        lines.append(f"- **主题**：{topic}")
        if goal:
            lines.append(f"- **目标**：{goal}")
        if uc:
            lines.append(f"- **背景摘录**：{uc}")
        fw = bool(pl.get("framework"))
        nsec = len(pl.get("sections") or {})
        nt = len(pl.get("teaching") or {})
        lines.append(f"- **进度标签**：大纲{'✓' if fw else '—'} · 章节要点块 {nsec} · 已生成讲解 {nt} 节")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def contexts_for_llm(max_per_project: int = 500) -> str:
    """Per-project user_context truncated for capability LLM."""
    parts: list[str] = []
    for meta in list_projects():
        raw = load_project(meta.project_id)
        if not raw:
            continue
        pl = (raw.get("pipeline") or {})
        st = pl.get("student") or {}
        topic = str(st.get("topic") or meta.title or meta.project_id).strip()
        uc = _truncate(str(st.get("user_context") or ""), max_per_project)
        if uc:
            parts.append(f"### {topic}\n{uc}")
    return "\n\n".join(parts).strip() or "（各项目未填写背景）"
