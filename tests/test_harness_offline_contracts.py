from __future__ import annotations

from prompts.registry import system_prompt


def test_system_prompt_registry_has_pipeline_prompts():
    # Contract: registry must provide system prompts for pipeline steps
    for name in (
        "books_recommend",
        "framework_chapters",
        "expand_chapter_sections",
        "expand_section_teaching",
        "teen_learning_loop",
        "teen_learning_loop_expand",
    ):
        s = system_prompt(name)
        assert isinstance(s, str) and s.strip()

