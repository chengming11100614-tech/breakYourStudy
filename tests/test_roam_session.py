from __future__ import annotations

from knowledge_graph import learned_node_id
from roam_session import (
    build_synthetic_item,
    extract_bridge_name,
    format_base_learned_cluster,
    graph_to_mermaid,
    new_roam_state,
    pick_one_liner,
    prepare_pool3,
    record_continue,
    record_first_pair,
    start_roam,
)


def test_pick_one_liner_from_bullet():
    md = "- 这是结论行\n\n更多内容"
    assert "这是结论行" in pick_one_liner(md)


def test_pick_one_liner_fallback():
    assert pick_one_liner("") == "上一轮关联结论"


def test_extract_bridge_name_from_first_line():
    md = "**桥梁名：** 微分与对偶\n\n一句话关联结论：……"
    assert extract_bridge_name(md) == "微分与对偶"


def test_extract_bridge_name_fallback():
    md = "只有正文没有桥梁名行"
    assert extract_bridge_name(md) == pick_one_liner(md)


def test_format_base_learned_cluster_lists_items():
    from knowledge_graph import learned_node_id

    items = [
        {"discipline": "D", "title": "A1", "summary": "s1", "keywords": ["k1"], "source_ref": "p:1"},
        {"discipline": "D", "title": "B2", "summary": "s2", "keywords": [], "source_ref": "p:2"},
    ]
    lk = {learned_node_id(x): x for x in items}
    ids = [learned_node_id(items[0]), learned_node_id(items[1])]
    out = format_base_learned_cluster(ids, lk)
    assert "A1" in out and "B2" in out
    assert "子知识点簇" in out


def test_build_synthetic_item_merges_keywords():
    from knowledge_graph import learned_node_id

    learned = [
        {"discipline": "D", "title": "A1", "summary": "s1", "keywords": ["alpha"], "source_ref": "p:1"},
        {"discipline": "D", "title": "B2", "summary": "s2", "keywords": ["beta"], "source_ref": "p:2"},
    ]
    id_a, id_b = learned_node_id(learned[0]), learned_node_id(learned[1])
    prev = {"bridge_name": "桥", "base_learned_ids": [id_a, id_b]}
    syn = build_synthetic_item("**桥梁名：** 桥\n\n正文", prev, learned=learned)
    assert "alpha" in syn["keywords"] and "beta" in syn["keywords"]


def test_start_roam_insufficient():
    st, msg = start_roam([{"title": "a", "discipline": "d", "summary": "s"}])
    assert st is None
    assert "至少" in msg or "2" in msg


def test_record_first_pair_and_continue_base_ids():
    learned = [
        {"discipline": "M", "title": "X", "summary": "sx", "keywords": [], "source_ref": "p:s1"},
        {"discipline": "M", "title": "Y", "summary": "sy", "keywords": [], "source_ref": "p:s2"},
        {"discipline": "M", "title": "Z", "summary": "sz", "keywords": [], "source_ref": "p:s3"},
    ]
    st, _ = start_roam(learned)
    assert st is not None
    id_x = learned_node_id(learned[0])
    id_y = learned_node_id(learned[1])
    id_z = learned_node_id(learned[2])
    pool_ids = {p["id"] for p in st["pool6"]}
    assert id_x in pool_ids and id_y in pool_ids
    record_first_pair(st, id_x, id_y, "**桥梁名：** 首轮桥\n\n- 一句话\n")
    assert st["virtual_nodes"][0]["base_learned_ids"] == sorted({id_x, id_y})
    assert st["virtual_nodes"][0]["bridge_name"] == "首轮桥"
    prepare_pool3(st, learned)
    record_continue(st, "v0", id_z, "md B")
    assert set(st["virtual_nodes"][-1]["base_learned_ids"]) == {id_x, id_y, id_z}


def test_graph_to_mermaid_has_transitive_edge():
    learned = [
        {"discipline": "M", "title": "X", "summary": "sx", "keywords": [], "source_ref": "p:s1"},
        {"discipline": "M", "title": "Y", "summary": "sy", "keywords": [], "source_ref": "p:s2"},
        {"discipline": "M", "title": "Z", "summary": "sz", "keywords": [], "source_ref": "p:s3"},
    ]
    lk = {learned_node_id(x): x for x in learned}
    id_x, id_y, id_z = learned_node_id(learned[0]), learned_node_id(learned[1]), learned_node_id(learned[2])
    st = new_roam_state()
    st["virtual_nodes"] = [
        {
            "id": "v0",
            "markdown": "m0",
            "one_liner": "AB",
            "left_id": id_x,
            "right_id": id_y,
            "left_kind": "learned",
            "right_kind": "learned",
            "base_learned_ids": sorted({id_x, id_y}),
        },
        {
            "id": "v1",
            "markdown": "m1",
            "one_liner": "BC",
            "left_id": "v0",
            "right_id": id_z,
            "left_kind": "virtual",
            "right_kind": "learned",
            "base_learned_ids": sorted({id_x, id_y, id_z}),
        },
    ]
    out = graph_to_mermaid(st, lk)
    assert "```mermaid" in out
    assert "间接" in out


