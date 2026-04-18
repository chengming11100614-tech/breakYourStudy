from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any


@dataclass(frozen=True)
class ProjectMeta:
    project_id: str
    title: str
    updated_at: str
    last_opened_at: str = ""


def _root() -> Path:
    return Path(__file__).resolve().parent / "data"


def _projects_dir() -> Path:
    return _root() / "projects"


def _exports_dir() -> Path:
    return _root() / "exports"


def _learned_path() -> Path:
    return _root() / "learned.json"


def _project_ids_on_disk() -> set[str]:
    d = _projects_dir()
    if not d.exists():
        return set()
    return {p.stem for p in d.glob("*.json")}


def _filter_learned_persisted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop entries tied to unsaved sessions (local:) or deleted project files."""
    pids = _project_ids_on_disk()
    out: list[dict[str, Any]] = []
    for it in items:
        ref = str(it.get("source_ref") or "").strip()
        if ref.startswith("local:"):
            continue
        if ":" in ref:
            pid, _, _ = ref.partition(":")
            pid = pid.strip()
            if pid and pid not in pids:
                continue
        out.append(it)
    return out


def _assoc_edges_path() -> Path:
    return _root() / "assoc_edges.json"


def _safe_id(s: str) -> str:
    keep = []
    for ch in s.strip():
        if ch.isalnum() or ch in ("-", "_"):
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip("_")
    return out or "project"


def new_project_id(topic: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{_safe_id(topic)[:32]}"


def list_projects() -> list[ProjectMeta]:
    d = _projects_dir()
    if not d.exists():
        return []
    items: list[ProjectMeta] = []
    for p in d.glob("*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            meta = raw.get("meta") or {}
            title = (meta.get("title") or meta.get("topic") or p.stem).strip()
            updated_at = (meta.get("updated_at") or "").strip()
            last_opened_at = (meta.get("last_opened_at") or "").strip()
            items.append(
                ProjectMeta(
                    project_id=p.stem,
                    title=title,
                    updated_at=updated_at,
                    last_opened_at=last_opened_at,
                )
            )
        except Exception:
            continue

    opened = [m for m in items if m.last_opened_at.strip()]
    rest = [m for m in items if not m.last_opened_at.strip()]
    opened.sort(key=lambda m: m.last_opened_at, reverse=True)
    rest.sort(key=lambda m: m.updated_at, reverse=True)
    return opened + rest


def touch_last_opened(project_id: str) -> None:
    pid = (project_id or "").strip()
    if not pid:
        return
    raw = load_project(pid)
    if not raw:
        return
    meta = dict(raw.get("meta") or {})
    meta["last_opened_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta.setdefault("project_id", pid)
    raw["meta"] = meta
    save_project(project_id=pid, payload=raw)


def save_project(*, project_id: str, payload: dict[str, Any]) -> None:
    d = _projects_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{project_id}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_project(project_id: str) -> dict[str, Any] | None:
    p = _projects_dir() / f"{project_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


_PROJECT_ID_SAFE = re.compile(r"^[\w-]+$")


def delete_project_files(project_ids: list[str]) -> list[str]:
    """Remove ``data/projects/{id}.json`` for each id. Only accepts safe id stems."""
    d = _projects_dir()
    deleted: list[str] = []
    for raw in project_ids:
        pid = (raw or "").strip()
        if not pid or not _PROJECT_ID_SAFE.match(pid):
            continue
        p = d / f"{pid}.json"
        if p.is_file():
            try:
                p.unlink()
                deleted.append(pid)
            except OSError:
                continue
    return deleted


def prune_learned_for_removed_projects(removed_project_ids: set[str]) -> int:
    """Remove learned entries whose ``source_ref`` is ``projectId:...`` for a deleted project id."""
    rm = {str(x).strip() for x in removed_project_ids if str(x).strip()}
    if not rm:
        return 0
    p = _learned_path()
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        raw = list(data) if isinstance(data, list) else []
    except Exception:
        return 0
    out: list[dict[str, Any]] = []
    n = 0
    for it in raw:
        ref = str(it.get("source_ref") or "").strip()
        if ":" in ref and not ref.startswith("local:"):
            pid, _, _ = ref.partition(":")
            if pid.strip() in rm:
                n += 1
                continue
        out.append(it)
    if n:
        save_learned(_filter_learned_persisted(out))
    return n


def delete_projects_selective(project_ids: list[str]) -> tuple[list[str], int]:
    """Delete project files then prune learned rows tied to those ids. Returns (deleted_ids, learned_removed_count)."""
    uniq = list(dict.fromkeys([(x or "").strip() for x in project_ids if (x or "").strip()]))
    deleted = delete_project_files(uniq)
    n_learned = prune_learned_for_removed_projects(set(deleted))
    return deleted, n_learned


def save_export(*, name: str, markdown: str) -> str:
    d = _exports_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{stamp}_{_safe_id(name)[:48]}.md"
    p = d / fname
    p.write_text(markdown, encoding="utf-8")
    return fname


def list_exports(limit: int = 30) -> list[str]:
    d = _exports_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
    return [f.name for f in files[: max(limit, 1)]]


def load_export(filename: str) -> str | None:
    p = _exports_dir() / filename
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def load_learned() -> list[dict[str, Any]]:
    p = _learned_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        raw = list(data) if isinstance(data, list) else []
    except Exception:
        return []
    out = _filter_learned_persisted(raw)
    if len(out) != len(raw):
        save_learned(out)
    return out


def save_learned(items: list[dict[str, Any]]) -> None:
    _root().mkdir(parents=True, exist_ok=True)
    p = _learned_path()
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def load_assoc_edges() -> dict[str, Any]:
    p = _assoc_edges_path()
    if not p.exists():
        return {"v": 1, "edges": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"v": 1, "edges": []}
        edges = data.get("edges")
        if not isinstance(edges, list):
            data["edges"] = []
        data.setdefault("v", 1)
        return data
    except Exception:
        return {"v": 1, "edges": []}


def save_assoc_edges(payload: dict[str, Any]) -> None:
    _root().mkdir(parents=True, exist_ok=True)
    p = _assoc_edges_path()
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_assoc_edge(
    *,
    item_a: dict[str, Any],
    item_b: dict[str, Any],
    analysis_preview: str,
    node_id_fn: Callable[[dict[str, Any]], str],
) -> None:
    """Persist one user association; same unordered pair overwrites previous."""
    a_id = node_id_fn(item_a)
    b_id = node_id_fn(item_b)
    if not a_id or not b_id or a_id == b_id:
        return
    pair_key = "|".join(sorted((a_id, b_id)))
    data = load_assoc_edges()
    old = list(data.get("edges") or [])
    edges: list[dict[str, Any]] = [e for e in old if str(e.get("pair_key") or "") != pair_key]
    snippet = (analysis_preview or "").strip().replace("\n", " ")
    if len(snippet) > 800:
        snippet = snippet[:799] + "…"
    edges.append(
        {
            "pair_key": pair_key,
            "a_id": a_id,
            "b_id": b_id,
            "a_title": str(item_a.get("title") or ""),
            "b_title": str(item_b.get("title") or ""),
            "a_discipline": str(item_a.get("discipline") or ""),
            "b_discipline": str(item_b.get("discipline") or ""),
            "analysis_preview": snippet,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    data["edges"] = edges
    save_assoc_edges(data)


def add_learned(new_items: list[dict[str, Any]]) -> int:
    """Append learned items; dedupe by (discipline,title,source_ref). Returns added count."""
    cur = load_learned()
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for it in cur:
        disc = str(it.get("discipline") or "")
        title = str(it.get("title") or "")
        ref = str(it.get("source_ref") or "")
        key = (disc, title, ref)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)

    added = 0
    for it in new_items:
        disc = str(it.get("discipline") or "")
        title = str(it.get("title") or "")
        ref = str(it.get("source_ref") or "")
        key = (disc, title, ref)
        if not disc or not title:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        added += 1

    save_learned(out)
    return added

