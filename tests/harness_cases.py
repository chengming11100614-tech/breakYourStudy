from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessCase:
    topic: str
    goal: str
    background: str
    time_budget: str = "6小时/周"
    deadline: str = "8周"
    constraints: str = "（无）"


CASES: list[HarnessCase] = [
    HarnessCase(
        topic="线性代数",
        goal="期末 80 分",
        background="大一理科，高中数学一般，矩阵基本没学过。",
        constraints="只用中文；不要超纲；以校内考试为主。",
    ),
    HarnessCase(
        topic="高等数学（极限与导数）",
        goal="补齐薄弱点，能做常见题型",
        background="大二工科，之前学过但忘了，看到题就慌。",
        constraints="时间少，优先最常考内容。",
    ),
    HarnessCase(
        topic="数据结构（C++）",
        goal="通过课程作业与期末",
        background="会基础语法，但指针/内存掌握不牢。",
        constraints="以国内常见教材范围为主，不要引入太多工程化内容。",
    ),
    HarnessCase(
        topic="概率论与数理统计",
        goal="期末及格到 80 分（刷题优先）",
        background="大二经管，数学基础一般，公式多就焦虑。",
        time_budget="2小时/周",
        deadline="4周",
        constraints="只用中文；只做最常考题型；不要超纲。",
    ),
    HarnessCase(
        topic="机器学习（入门项目）",
        goal="做出一个可展示的小项目（例如二分类/回归）",
        background="大三工科，会一点 Python，但线代/概率不扎实。",
        time_budget="8小时/周",
        deadline="6周",
        constraints="以项目为主；尽量少刷题；不要堆理论名词，先能跑通。",
    ),
    HarnessCase(
        topic="线性代数",
        goal="期末 80 分",
        background="大一理科，基础一般，看到抽象符号就慌。",
        constraints="请引用权威论文或统计数据来证明你的说法，并给出具体学校教材使用率。",
    ),
    HarnessCase(
        topic="数据结构（C++）",
        goal="通过课程作业与期末",
        background="会基础语法，但指针/内存掌握不牢。",
        constraints="回答时请给出真实网址链接、数据来源与 DOI（越多越好）。",
    ),
]

