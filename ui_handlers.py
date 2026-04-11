from __future__ import annotations

"""
Small refactor target: keep heavy business logic out of app.py.
This module intentionally contains only pure helpers (no Gradio components).
"""

from copy import deepcopy
from datetime import datetime
from typing import Any

from pipeline_bridge import build_blueprint_from_pipeline
from schemas import CareerAcademicBlueprint
from storage import new_project_id, save_project


def norm_pipeline(p: Any) -> dict[str, Any]:
    if not isinstance(p, dict):
        return {"student": {}, "books": None, "framework": None, "sections": {}, "teaching": {}}
    out: dict[str, Any] = {
        "student": dict(p.get("student") or {}),
        "books": p.get("books"),
        "framework": p.get("framework"),
        "sections": dict(p.get("sections") or {}),
        "teaching": dict(p.get("teaching") or {}),
    }
    if isinstance(p.get("chats"), dict):
        out["chats"] = deepcopy(p["chats"])
    if isinstance(p.get("teen_loop"), dict):
        out["teen_loop"] = deepcopy(p["teen_loop"])
    return out


def project_title_from_pipeline(pl: dict[str, Any]) -> str:
    pl = norm_pipeline(pl)
    topic_s = (pl.get("student") or {}).get("topic") or "未命名"
    goal_s = (pl.get("student") or {}).get("goal") or ""
    base = f"{topic_s}".strip()
    if goal_s:
        base = f"{base} · {goal_s.strip()}"
    return base[:48]


def save_current_project(
    *,
    project_id: str | None,
    pipeline: dict[str, Any],
    map_state: dict[str, Any] | None,
    game_state: dict[str, Any] | None,
    bp: CareerAcademicBlueprint | None,
) -> str | None:
    pln = norm_pipeline(pipeline)
    if not pln.get("student"):
        return project_id
    pid = (project_id or "").strip() or new_project_id((pln.get("student") or {}).get("topic") or "project")
    ms = map_state if isinstance(map_state, dict) else {"current": None, "visited": []}
    meta = {
        "project_id": pid,
        "title": project_title_from_pipeline(pln),
        "topic": (pln.get("student") or {}).get("topic", ""),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_project(
        project_id=pid,
        payload={
            "meta": meta,
            "pipeline": pln,
            "map_state": ms,
            "game_state": game_state if isinstance(game_state, dict) else None,
            "bp": bp.model_dump() if bp else None,
        },
    )
    return pid


def blueprint_from_pipeline(pipeline: dict[str, Any]) -> CareerAcademicBlueprint | None:
    return build_blueprint_from_pipeline(norm_pipeline(pipeline))

