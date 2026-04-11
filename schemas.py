from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class RelationType(str, Enum):
    requires = "requires"
    enriches = "enriches"
    can_parallel_if = "can_parallel_if"


class DisciplinaryLogic(BaseModel):
    core_question: str = Field(min_length=1)
    reasoning_chain: list[str] = Field(min_length=1)
    bad_orders: list[str] = Field(default_factory=list)


class Track(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class ExerciseKind(str, Enum):
    concept = "concept"
    calculation = "calculation"
    short_answer = "short_answer"
    design = "design"


class Exercise(BaseModel):
    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    kind: ExerciseKind
    hint: str | None = None
    answer_outline: str = Field(min_length=1)


class Teaching(BaseModel):
    explain: str = Field(min_length=1)
    key_points: list[str] = Field(min_length=3)
    common_pitfalls: list[str] = Field(default_factory=list)


class SkillNode(BaseModel):
    id: str = Field(min_length=1)
    track_id: str = Field(min_length=1)
    title: str = Field(min_length=1)

    what_to_learn: str = Field(min_length=1)
    how_to_learn: str = Field(min_length=1)
    practice: str = Field(min_length=1)

    prerequisite_ids: list[str] = Field(default_factory=list)
    why_prerequisites: list[str] = Field(default_factory=list)

    position_in_logic: str | None = None

    # Stage 2 fields
    teaching: Teaching | None = None
    exercises: list[Exercise] = Field(default_factory=list)


class CrossEdge(BaseModel):
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    relation: RelationType
    why: str = Field(min_length=1)


class SynthesisMilestone(BaseModel):
    title: str = Field(min_length=1)
    involved_node_ids: list[str] = Field(min_length=1)
    deliverables: list[str] = Field(min_length=1)


class InterdisciplineConceptRef(BaseModel):
    discipline: str = Field(min_length=1)
    concept: str = Field(min_length=1)


class InterdisciplineEdge(BaseModel):
    from_ref: InterdisciplineConceptRef = Field(alias="from")
    to_ref: InterdisciplineConceptRef = Field(alias="to")
    relation: Literal["applies", "grounds", "analogous", "constraints"]
    mechanism: str = Field(min_length=1)


class CareerAcademicBlueprint(BaseModel):
    meta: dict[str, str] = Field(default_factory=dict)
    disciplinary_logic: DisciplinaryLogic

    tracks: list[Track] = Field(min_length=1)
    nodes: list[SkillNode] = Field(min_length=1)
    cross_edges: list[CrossEdge] = Field(default_factory=list)
    synthesis_milestones: list[SynthesisMilestone] = Field(default_factory=list)

    interdiscipline_edges: list[InterdisciplineEdge] = Field(default_factory=list)


# --- 书籍驱动四步管线（荐书 → 章框架 → 小节骨架 → 单节展开） ---


class AuthoritativeBook(BaseModel):
    title: str = Field(min_length=1)
    authors: str = Field(min_length=1)
    edition_or_year_hint: str | None = None
    why_global_standard: str = Field(min_length=1)
    suitable_for_this_student: str = Field(min_length=1)
    study_role: Literal["主教材", "参考书", "习题或方法册", "思想史或原典导读"]


class BooksRecommendResult(BaseModel):
    books: list[AuthoritativeBook] = Field(min_length=3, max_length=5)
    disclaimer: str = Field(min_length=1)


class FrameworkChapter(BaseModel):
    chapter_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    detailed_toc: list[str] = Field(min_length=6, max_length=14)
    core_ideas: str = Field(min_length=1)
    learning_method: str = Field(min_length=1)
    book_reference_note: str = Field(min_length=1)


class ChapterFrameworkResult(BaseModel):
    meta: dict[str, str] = Field(default_factory=dict)
    disciplinary_logic: DisciplinaryLogic
    global_learning_method: str = Field(min_length=1)
    chapters: list[FrameworkChapter] = Field(min_length=5, max_length=12)


class CourseSection(BaseModel):
    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    knowledge_points: list[str] = Field(min_length=2, max_length=10)


class ChapterSectionsResult(BaseModel):
    chapter_id: str = Field(min_length=1)
    sections: list[CourseSection] = Field(min_length=2, max_length=10)


class GoalPractice(BaseModel):
    """本节内容与用户总目标之间的可操作桥梁（实战演练）。"""

    toward_goal: str = Field(min_length=1, description="本节如何推进用户声明的学习目标")
    scenario: str = Field(min_length=1, description="具体情境与约束，便于着手练习")
    steps: list[str] = Field(min_length=3, max_length=8, description="可执行步骤")
    self_check: list[str] = Field(min_length=2, max_length=10, description="自评与验收要点")


class SectionTeachingExpand(BaseModel):
    teaching: Teaching
    exercises: list[Exercise] = Field(min_length=1)
    goal_practice: GoalPractice | None = None

