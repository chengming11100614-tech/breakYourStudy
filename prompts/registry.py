from __future__ import annotations

import os

from . import (
    assoc_analyze,
    assoc_qa,
    books_recommend,
    capability_reflect,
    chat_intake,
    expand_chapter_sections,
    expand_section_teaching,
    framework_chapters,
    judge_compare,
    node_teaching,
    path_structure,
    section_qa,
    teen_learning_loop,
    teen_learning_loop_expand,
)


def get_profile() -> str:
    return (os.getenv("PROMPT_PROFILE") or "default").strip().lower()


def system_prompt(name: str) -> str:
    # For now, one profile. Later you can branch by profile here.
    if name == "books_recommend":
        return books_recommend.SYSTEM
    if name == "framework_chapters":
        return framework_chapters.SYSTEM
    if name == "expand_chapter_sections":
        return expand_chapter_sections.SYSTEM
    if name == "expand_section_teaching":
        return expand_section_teaching.SYSTEM
    if name == "path_structure":
        return path_structure.SYSTEM
    if name == "node_teaching":
        return node_teaching.SYSTEM
    if name == "chat_intake":
        return chat_intake.SYSTEM
    if name == "teen_learning_loop":
        return teen_learning_loop.SYSTEM
    if name == "teen_learning_loop_expand":
        return teen_learning_loop_expand.SYSTEM
    if name == "assoc_analyze":
        return assoc_analyze.SYSTEM
    if name == "assoc_qa":
        return assoc_qa.SYSTEM
    if name == "section_qa":
        return section_qa.SYSTEM
    if name == "judge_compare":
        return judge_compare.SYSTEM
    if name == "capability_reflect":
        return capability_reflect.SYSTEM
    raise KeyError(f"Unknown prompt: {name}")


def user_prompt(name: str, **kwargs: str) -> str:
    if name == "path_structure":
        return path_structure.user_prompt(**kwargs)
    if name == "books_recommend":
        return books_recommend.user_prompt(**kwargs)
    if name == "framework_chapters":
        return framework_chapters.user_prompt(**kwargs)
    if name == "expand_chapter_sections":
        return expand_chapter_sections.user_prompt(**kwargs)
    if name == "expand_section_teaching":
        return expand_section_teaching.user_prompt(**kwargs)
    if name == "node_teaching":
        return node_teaching.user_prompt(**kwargs)
    if name == "teen_learning_loop":
        return teen_learning_loop.user_prompt(**kwargs)
    if name == "teen_learning_loop_expand":
        return teen_learning_loop_expand.user_prompt(**kwargs)
    if name == "assoc_analyze":
        return assoc_analyze.user_prompt(**kwargs)
    if name == "assoc_qa":
        return assoc_qa.user_prompt(**kwargs)
    if name == "section_qa":
        return section_qa.user_prompt(**kwargs)
    if name == "judge_compare":
        return judge_compare.user_prompt(**kwargs)
    if name == "capability_reflect":
        return capability_reflect.user_prompt(**kwargs)
    raise KeyError(f"Unknown prompt: {name}")

