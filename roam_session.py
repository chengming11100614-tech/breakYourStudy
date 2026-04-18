from __future__ import annotations

import random
import re
from typing import Any

from knowledge_graph import learned_node_id

PHASE_IDLE = "idle"
PHASE_PICK_TWO = "pick_two"
PHASE_PICK_ONE = "pick_one"
PHASE_DONE = "done"


def new_roam_state() -> dict[str, Any]:
    return {
        "phase": PHASE_IDLE,
        "virtual_nodes": [],
        "used_learned_ids": [],
        "pool6": [],
        "pool3": [],
        "next_pool_notice": "",
    }


def _items_with_ids(learned: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for it in learned:
        lid = learned_node_id(it)
        if lid:
            out.append((lid, it))
    return out


def random_learned_pool(
    items: list[tuple[str, dict[str, Any]]],
    k: int,
    exclude: set[str],
) -> list[tuple[str, dict[str, Any]]]:
    cand = [(lid, d) for lid, d in items if lid not in exclude]
    if not cand:
        return []
    random.shuffle(cand)
    return cand[: min(k, len(cand))]


def extract_bridge_name(markdown: str) -> str:
    """Parse `**桥梁名：** xxx` (or plain 桥梁名：) from assoc output; else pick_one_liner."""
    t = (markdown or "").strip()
    for line in t.splitlines():
        s = line.strip()
        if not s:
            continue
        plain = re.sub(r"\*+", "", s).strip()
        m = re.search(r"桥梁名\s*[：:]\s*(.+)$", plain)
        if m:
            name = m.group(1).strip()
            if name:
                return name[:80]
    return pick_one_liner(markdown)


def pick_one_liner(md: str) -> str:
    t = (md or "").strip()
    if not t:
        return "上一轮关联结论"
    for line in t.splitlines():
        s = line.strip()
        if not s:
            continue
        if "一句话" in s and "结论" in s:
            m = re.search(r"[：:]\s*(.+)", s)
            if m:
                return m.group(1).strip()[:120]
        if s.startswith("-") or s.startswith("*"):
            inner = s.lstrip("-*").strip()
            if inner:
                return inner[:120]
        if len(s) > 5 and not s.startswith("#"):
            return s[:120]
    return t.splitlines()[0][:120] if t else "上一轮关联结论"


def _learned_by_id_map(learned: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {learned_node_id(it): dict(it) for it in learned if learned_node_id(it)}


def format_base_learned_cluster(
    base_learned_ids: list[str],
    learned_by_id: dict[str, dict[str, Any]],
    *,
    max_summary_each: int = 260,
) -> str:
    """
    Human-readable block: all learned ids under the current bridge, for LLM context.
    Instructs the model to treat them as a mutually linked cluster, not isolated facts.
    """
    ids = [str(x).strip() for x in (base_learned_ids or []) if str(x).strip()]
    if not ids:
        return ""
    lines: list[str] = [
        "【桥梁已覆盖的已学子知识点簇】",
        "下列条目已通过本轮漫游中的各步关联被纳入同一座「桥梁」之下，请把它们视为**彼此已通过前述分析形成联系的整体**，不要只抓住其中一两个点而忽略其余。",
        "",
    ]
    for lid in ids:
        it = learned_by_id.get(lid) or {}
        t = str(it.get("title") or "").strip() or lid[:20]
        d = str(it.get("discipline") or "").strip()
        summ = str(it.get("summary") or "").strip().replace("\n", " ")
        if len(summ) > max_summary_each:
            summ = summ[: max_summary_each - 1] + "…"
        kw = "、".join(str(x).strip() for x in (it.get("keywords") or [])[:16] if str(x).strip())
        head = f"- **{t}**" + (f"（{d}）" if d else "")
        lines.append(head)
        if summ:
            lines.append(f"  - 摘要：{summ}")
        if kw:
            lines.append(f"  - 关键词：{kw}")
        lines.append("")
    lines.append(
        "请在把「当前桥梁结论」与**新选中的已学知识点 B**做关联时，显式引用上述子知识点之间的内在联系，并说明新点 B 如何嵌入或扩展这一簇关系。"
    )
    return "\n".join(lines).strip()


def merge_keywords_from_base(
    base_learned_ids: list[str],
    learned_by_id: dict[str, dict[str, Any]],
    *,
    cap: int = 28,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for lid in base_learned_ids:
        it = learned_by_id.get(lid) or {}
        for x in it.get("keywords") or []:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= cap:
                return out
    return out


def build_synthetic_item(
    prev_md: str,
    prev_node: dict[str, Any] | None = None,
    *,
    learned: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bn = ""
    base_ids: list[str] = []
    if isinstance(prev_node, dict):
        bn = str(prev_node.get("bridge_name") or "").strip()
        base_ids = [str(x) for x in (prev_node.get("base_learned_ids") or []) if str(x).strip()]
    title = bn or pick_one_liner(prev_md)
    summary = (prev_md or "").strip()[:800]
    kw_out: list[str] = []
    if learned is not None and base_ids:
        by_id = _learned_by_id_map(learned)
        kw_out = merge_keywords_from_base(base_ids, by_id, cap=28)
    return {
        "discipline": "关联漫游",
        "title": title,
        "summary": summary or "（上一轮关联分析全文见下方）",
        "keywords": kw_out,
        "source_ref": "",
    }


def start_roam(learned: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """Begin session: sample up to 6 items. Returns (state, status_message)."""
    items = _items_with_ids(learned)
    if len(items) < 2:
        return None, "🟡 已学库至少需要 2 条才能开始漫游（请先在「小节详解」点「已学会」）。"
    pool = random_learned_pool(items, 6, set())
    st = new_roam_state()
    st["phase"] = PHASE_PICK_TWO
    st["pool6"] = [{"id": lid, "item": dict(it)} for lid, it in pool]
    st["used_learned_ids"] = []
    st["pool3"] = []
    st["next_pool_notice"] = ""
    n = len(pool)
    msg = f"✅ 已随机抽出 **{n}** 条已学知识点，请**恰好勾选 2 个**，再点「确认两步关联」。"
    return st, msg


def _base_ids_for_parent(state: dict[str, Any], parent_id: str, parent_kind: str) -> set[str]:
    if parent_kind == "learned":
        return {parent_id}
    for vn in state.get("virtual_nodes") or []:
        if vn.get("id") == parent_id:
            return set(vn.get("base_learned_ids") or [])
    return set()


def record_first_pair(state: dict[str, Any], id_a: str, id_b: str, markdown: str) -> dict[str, Any]:
    """After LLM assoc for two learned items."""
    if id_a == id_b:
        raise ValueError("same id")
    pool_ids = {p["id"] for p in state.get("pool6") or []}
    if id_a not in pool_ids or id_b not in pool_ids:
        raise ValueError("ids not in pool6")
    vid = f"v{len(state.get('virtual_nodes') or [])}"
    base = {id_a, id_b}
    bridge = extract_bridge_name(markdown)
    node = {
        "id": vid,
        "markdown": markdown,
        "bridge_name": bridge,
        "one_liner": bridge,
        "left_id": id_a,
        "right_id": id_b,
        "left_kind": "learned",
        "right_kind": "learned",
        "base_learned_ids": sorted(base),
    }
    state.setdefault("virtual_nodes", []).append(node)
    used = set(state.get("used_learned_ids") or [])
    used |= base
    state["used_learned_ids"] = sorted(used)
    state["phase"] = PHASE_PICK_ONE
    state["pool3"] = []
    state["next_pool_notice"] = ""
    return state


def prepare_pool3(state: dict[str, Any], learned: list[dict[str, Any]]) -> dict[str, Any]:
    """Sample 3 learned items excluding used_learned_ids."""
    items = _items_with_ids(learned)
    exclude = set(state.get("used_learned_ids") or [])
    pool = random_learned_pool(items, 3, exclude)
    state["pool3"] = [{"id": lid, "item": dict(it)} for lid, it in pool]
    if len(pool) < 3:
        state["next_pool_notice"] = f"_（可选项仅 {len(pool)} 条：已学库中其余条目已在本次漫游中用过或不足。）_"
    else:
        state["next_pool_notice"] = "_请选择一个已学知识点，与**当前桥梁**继续关联。_"
    return state


def record_continue(state: dict[str, Any], prev_vid: str, learned_id: str, markdown: str) -> dict[str, Any]:
    """Prev round's virtual node id + newly chosen learned id."""
    vnodes = state.get("virtual_nodes") or []
    if not vnodes or vnodes[-1]["id"] != prev_vid:
        raise ValueError("prev_vid mismatch")
    prev = vnodes[-1]
    base_prev = set(prev.get("base_learned_ids") or [])
    base_new = set(base_prev) | {learned_id}
    vid = f"v{len(vnodes)}"
    bridge = extract_bridge_name(markdown)
    node = {
        "id": vid,
        "markdown": markdown,
        "bridge_name": bridge,
        "one_liner": bridge,
        "left_id": prev_vid,
        "right_id": learned_id,
        "left_kind": "virtual",
        "right_kind": "learned",
        "base_learned_ids": sorted(base_new),
    }
    vnodes.append(node)
    used = set(state.get("used_learned_ids") or [])
    used.add(learned_id)
    state["used_learned_ids"] = sorted(used)
    state["pool3"] = []
    state["next_pool_notice"] = ""
    return state


def finish_roam(state: dict[str, Any]) -> dict[str, Any]:
    state["phase"] = PHASE_DONE
    return state


def _label_for_learned(item: dict[str, Any], lid: str) -> str:
    t = str(item.get("title") or "").strip() or lid[:20]
    d = str(item.get("discipline") or "").strip()
    if d:
        return f"{d[:8]}·{t[:24]}"
    return t[:32]


def _mermaid_node_id(prefix: str, i: int) -> str:
    return f"{prefix}{i}"


def graph_to_mermaid(state: dict[str, Any], learned_lookup: dict[str, dict[str, Any]]) -> str:
    """Mermaid flowchart: structural edges + transitive learned-learned edges."""
    vnodes = state.get("virtual_nodes") or []
    if not vnodes:
        return "_（尚未形成关联链）_\n"

    learned_map: dict[str, str] = {}
    li = 0
    for vn in vnodes:
        for kind, lid in ((vn.get("left_kind"), vn.get("left_id")), (vn.get("right_kind"), vn.get("right_id"))):
            if kind == "learned" and lid and lid not in learned_map:
                learned_map[lid] = _mermaid_node_id("L", li)
                li += 1
        for lid in vn.get("base_learned_ids") or []:
            if lid not in learned_map:
                learned_map[lid] = _mermaid_node_id("L", li)
                li += 1

    virt_map = {vn["id"]: _mermaid_node_id("R", i) for i, vn in enumerate(vnodes)}

    lines: list[str] = [
        "```mermaid",
        "flowchart LR",
    ]

    for lid, mid in learned_map.items():
        it = learned_lookup.get(lid, {})
        lab = _label_for_learned(it, lid).replace('"', "'")
        lines.append(f'  {mid}["{lab}"]')

    for vn in vnodes:
        vid = virt_map[vn["id"]]
        lab = (vn.get("bridge_name") or vn.get("one_liner") or vn["id"]).replace('"', "'")[:48]
        lines.append(f'  {vid}("{lab}")')

    struct_edges: set[tuple[str, str]] = set()
    trans_edges: set[tuple[str, str]] = set()

    for vn in vnodes:
        v_mid = virt_map[vn["id"]]
        lk, rk = vn.get("left_kind"), vn.get("right_kind")
        lid, rid = vn.get("left_id"), vn.get("right_id")
        if lk == "learned" and lid in learned_map:
            a, b = learned_map[lid], v_mid
            struct_edges.add(tuple(sorted((a, b))))
        elif lk == "virtual" and lid in virt_map:
            a, b = virt_map[lid], v_mid
            struct_edges.add(tuple(sorted((a, b))))
        if rk == "learned" and rid in learned_map:
            a, b = learned_map[rid], v_mid
            struct_edges.add(tuple(sorted((a, b))))
        elif rk == "virtual" and rid in virt_map:
            a, b = virt_map[rid], v_mid
            struct_edges.add(tuple(sorted((a, b))))

        if lk == "virtual" and rk == "learned" and rid in learned_map:
            z_mid = learned_map[rid]
            base = _base_ids_for_parent(state, lid, "virtual")
            for b in base:
                if b in learned_map and learned_map[b] != z_mid:
                    trans_edges.add(tuple(sorted((z_mid, learned_map[b]))))
        elif rk == "virtual" and lk == "learned" and lid in learned_map:
            z_mid = learned_map[lid]
            base = _base_ids_for_parent(state, rid, "virtual")
            for b in base:
                if b in learned_map and learned_map[b] != z_mid:
                    trans_edges.add(tuple(sorted((z_mid, learned_map[b]))))

    for a, b in sorted(struct_edges):
        lines.append(f"  {a} --- {b}")
    for a, b in sorted(trans_edges):
        if (a, b) not in struct_edges:
            lines.append(f"  {a} -.->|间接| {b}")

    lines.append("```")
    lines.append("")
    lines.append("**结构说明**：实线表示本轮关联的直接组合；虚线表示「经上一轮综合结论传递」而在知识网上显示的关联。")
    return "\n".join(lines)


def learned_lookup_from_list(learned: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {learned_node_id(it): it for it in learned if learned_node_id(it)}
