from __future__ import annotations

import copy
import json
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import time

import gradio as gr
from dotenv import load_dotenv
from pydantic import BaseModel

from llm_client import (
    LLMError,
    chat_json,
    chat_json_multi,
    chat_json_multi_continue,
    chat_text,
    chat_text_multi,
    first_submit_overlap_enabled,
    first_submit_passes_from_env,
    load_config_for_qa,
    load_config_from_env,
    load_parallel_llm_config_optional,
    maybe_warmup_llm,
)
from pipeline_bridge import (
    books_to_markdown,
    build_blueprint_from_pipeline,
    chapter_sections_to_markdown,
    framework_to_markdown,
    pipeline_export_markdown,
    section_teaching_to_markdown,
)
from prompts.registry import system_prompt, user_prompt
from knowledge_graph import learned_node_id
from roam_session import (
    PHASE_PICK_ONE,
    PHASE_PICK_TWO,
    build_synthetic_item,
    finish_roam,
    format_base_learned_cluster,
    graph_to_mermaid,
    learned_lookup_from_list,
    new_roam_state,
    prepare_pool3,
    record_continue,
    record_first_pair,
    start_roam,
)
from storage import (
    delete_projects_selective,
    list_projects,
    load_project,
    save_export,
    touch_last_opened,
    upsert_assoc_edge,
)
from ui_handlers import norm_pipeline as _norm_pipeline
from ui_handlers import project_title_from_pipeline as _project_title_from_pl
from ui_handlers import save_current_project as _save_current_project
from storage import add_learned, load_learned
from schemas import (
    BooksRecommendResult,
    CareerAcademicBlueprint,
    ChapterFrameworkResult,
    ChapterSectionsResult,
    SectionTeachingExpand,
    SkillNode,
)


def _realign_framework_to_books(cfg, fw: ChapterFrameworkResult, br: BooksRecommendResult) -> ChapterFrameworkResult:
    """One-shot sync: outline was drafted on pass-1 books; patch book-facing fields to match final books JSON."""
    prev = json.dumps(fw.model_dump(), ensure_ascii=False, indent=2)
    books = json.dumps(br.model_dump(), ensure_ascii=False, indent=2)
    u = (
        "【最终荐书 JSON】\n"
        + books
        + "\n\n【当前章级大纲 JSON】\n"
        + prev
        + "\n\n请输出与第二步章级框架**完全相同 schema** 的一份 JSON。保持 chapters 的章节顺序、chapter_id、title、detailed_toc、core_ideas、learning_method 的教学主干不变（仅可做轻微措辞修正）；"
        "**重点**：修订各章 book_reference_note，以及 global_learning_method、disciplinary_logic、meta 中涉及「书目、教材、哪本书」的表述，使其与【最终荐书 JSON】严格一致，不要引入荐书中不存在的书。"
    )
    return chat_json(
        cfg=cfg,
        system=system_prompt("framework_chapters"),
        user=u,
        schema_model=ChapterFrameworkResult,
    )


def _first_free_port(start: int, host: str, *, max_tries: int = 24) -> int:
    """Use start, or the next free port in [start, start+max_tries), if bind succeeds."""
    bind_host = host if host in ("0.0.0.0", "::", "") else "127.0.0.1"
    for i in range(max_tries):
        port = start + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((bind_host, port))
                return port
            except OSError:
                continue
    return start


# Gradio 默认主要识别块级 ``$$``；模型常用行内 ``$...$``，需显式声明（``$$`` 放前避免被单个 ``$`` 截断）。
MARKDOWN_LATEX_DELIMITERS: list[dict[str, str | bool]] = [
    {"left": "$$", "right": "$$", "display": True},
    {"left": "$", "right": "$", "display": False},
    {"left": r"\(", "right": r"\)", "display": False},
    {"left": r"\[", "right": r"\]", "display": True},
]


def _md(value: str | None = None, **kwargs) -> gr.Markdown:
    kwargs.setdefault("latex_delimiters", MARKDOWN_LATEX_DELIMITERS)
    return gr.Markdown(value, **kwargs)


# region agent log
def _dbg_log(runId: str, hypothesisId: str, location: str, message: str, data: dict) -> None:
    try:
        payload = {
            "sessionId": "91b955",
            "runId": runId,
            "hypothesisId": hypothesisId,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("debug-91b955.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# endregion agent log


CUSTOM_CSS = r"""
/* Forest-like minimalist cartoon style
   Gradio 6.x 使用带版本后缀的容器类（如 .gradio-container-6-10-0），仅写 .gradio-container 时样式/布局修正可能完全不生效 */
.gradio-container,
div[class^="gradio-container-"] {
  background-color: #FDFCF8 !important;
  font-family: "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, sans-serif;
  font-size: 14px !important;
  /* Tabs 底部分隔线、blockquote 左边线等用 primary 绿；改为中性灰去掉「绿框」感 */
  --border-color-primary: #d0d0cc !important;
  /* 根容器外沿：压掉 theme 里 panel/block 的 1px 描边（绿 primary 易被看成整页绿框） */
  --panel-border-width: 0px !important;
  --block-border-width: 0px !important;
  border: none !important;
  outline: none !important;
}

/* 工作台铺满视口：取消 1280 居中限宽（首页内部仍可用 .landing-anim-wrap 等自控宽度） */
.gradio-container .wrap,
.gradio-container .container,
div[class^="gradio-container-"] .wrap,
div[class^="gradio-container-"] .container {
  max-width: none !important;
  width: 100% !important;
  margin: 0 !important;
  box-sizing: border-box !important;
  border: none !important;
  outline: none !important;
}
/* 主列占满可视高度，避免四周大块留白（为 Gradio 底栏预留约 52px） */
div[class^="gradio-container-"] .wrap {
  min-height: calc(100vh - 52px) !important;
  min-height: calc(100dvh - 52px) !important;
}
div[class^="gradio-container-"] main.contain {
  min-height: calc(100vh - 52px) !important;
  min-height: calc(100dvh - 52px) !important;
  border: none !important;
  outline: none !important;
}

/* 勿对 html/body 锁死 height + overflow:hidden：在 Gradio 6 下易把主区压成「白屏」 */

/* Right: keep one vertical scroll area for blueprint（顶栏压缩后多占垂直空间） */
#project_right_panel {
  height: calc(100vh - 80px);
  height: calc(100dvh - 80px);
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  border: none !important;
  box-shadow: none !important;
}

/* Left: keep within viewport; allow inner scroll */
#project_left_panel {
  padding: 0 !important;
  height: calc(100vh - 80px) !important;
  max-height: calc(100vh - 80px) !important;
  height: calc(100dvh - 80px) !important;
  max-height: calc(100dvh - 80px) !important;
  overflow-x: hidden !important;
  overflow-y: auto !important;
  overscroll-behavior: contain;
  align-self: flex-start !important;
}
#left_switch {
  height: auto !important;
  max-height: none !important;
  overflow: visible !important;
  align-self: flex-start !important;
}

/* Keep only left-panel scrolling; avoid nested scroll boxes inside */
#project_left_panel .form,
#project_left_panel .gr-form,
#project_left_panel .gr-block,
#project_left_panel .block,
#project_left_panel .wrap,
#project_left_panel .contain {
  overflow: visible !important;
  max-height: none !important;
}

/* 左侧两页切换：仅用可见性切换，禁止 absolute 叠层（否则隐藏页仍会盖住可点击区域） */
#left_pages_stack {
  position: relative;
  min-height: 0;
}

/* 彻底移除隐藏页的占位（避免切换后出现空白块） */
#left_pages_stack [hidden],
#left_pages_stack .hidden,
#left_pages_stack .hide {
  display: none !important;
}

.left-page {
  padding: 18px !important;
  box-sizing: border-box;
  height: auto !important;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
#project_left_panel .left-page .gr-textbox,
#project_left_panel .left-page .gr-button {
  flex: 0 0 auto;
}

/* Make text inputs more compact (keep overall height <= viewport) */
#project_left_panel textarea {
  min-height: 52px !important;
  max-height: 96px !important;
}

/* Left-side page switch buttons */
.left-switch {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 18px;
}
/* Force exact size for the two side buttons */
#left_switch, .left-switch {
  height: auto !important;
  align-self: flex-start !important;
}
#left_switch .gr-button, .left-switch .gr-button,
#left_switch .button-wrap, .left-switch .button-wrap {
  flex: 0 0 auto !important;
  height: auto !important;
}
#left_switch button, .left-switch button,
#left_switch .gr-button > button, .left-switch .gr-button > button {
  height: 42px !important;
  min-height: 42px !important;
  line-height: 42px !important;
  width: 62px !important;
  min-width: 62px !important;
  padding: 0 !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
  font-size: 0.85rem !important;
  opacity: 0.95 !important;
}

/* 去掉「绿框」感：不要描边与绿色投影，与整页铺底融为一体 */
.forest-card {
  background: white !important;
  border-radius: 12px !important;
  border: none !important;
  box-shadow: none !important;
  padding: 18px !important;
  min-height: 0px;
}
.forest-card:hover {
  transform: none;
  box-shadow: none !important;
}

.hero-title h1, .hero-title {
  font-size: 2.4rem !important;
  color: #4A634D !important;
  font-weight: 900 !important;
  letter-spacing: -1px !important;
  margin-bottom: 0.4rem !important;
}

.section-title {
  color: #5F865E !important;
  font-weight: 800 !important;
  border-bottom: 2px solid #E2E8CE !important;
  padding-bottom: 8px !important;
  margin-bottom: 14px !important;
}

/* Left intake: no underline — users read this as unwanted “green thin lines” */
#project_left_panel .section-title {
  border-bottom: none !important;
  box-shadow: none !important;
  padding-bottom: 4px !important;
  margin-bottom: 6px !important;
}

/* 附加功能侧栏：减少顶部空白、略压缩内边距 */
#project_left_panel .left-page-extra.forest-card {
  padding: 10px 14px 12px !important;
}
/* 打掉 Markdown 标题默认的上外边距，避免「附加」页顶一大块空 */
#project_left_panel .left-page-extra .md,
#project_left_panel .left-page-extra .prose {
  margin-top: 0 !important;
}
#project_left_panel .left-page-extra .prose h1,
#project_left_panel .left-page-extra .prose h2,
#project_left_panel .left-page-extra .prose h3,
#project_left_panel .left-page-extra .prose h4 {
  margin-top: 0.35em !important;
  margin-bottom: 0.4em !important;
}
#project_left_panel .left-page-extra .prose h4:first-child {
  margin-top: 0 !important;
}

/* Project header: two actions stay compact left (not half-screen wide) */
#project_top_bar {
  flex-wrap: wrap !important;
  align-items: center !important;
  column-gap: 12px !important;
}
#project_top_btns {
  flex: 0 0 auto !important;
  width: auto !important;
}
#project_top_btns button,
#project_top_btns .gr-button,
#project_top_btns .gr-button > button {
  width: auto !important;
  min-width: 0 !important;
  flex: 0 0 auto !important;
}

/* Buttons: unified height and padding */
.gradio-container button,
div[class^="gradio-container-"] button {
  height: 42px !important;
  padding: 0 16px !important;
  border-radius: 16px !important;
  font-weight: 800 !important;
}

/* Buttons: try to target gradio button in a safe way */
.btn-primary button, .btn-primary {
  background: #7BAE7F !important;
  color: white !important;
  border: none !important;
}
.btn-secondary button, .btn-secondary {
  background: #7BAE7F !important;
  color: white !important;
  border-radius: 16px !important;
  border: none !important;
  font-weight: 800 !important;
  opacity: 0.92 !important;
}

/* Disabled button: more gray and clearly inactive */
.gradio-container button:disabled,
div[class^="gradio-container-"] button:disabled {
  filter: grayscale(1) !important;
  opacity: 0.55 !important;
  cursor: not-allowed !important;
}

.status-card {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  padding: 6px 0 !important;
  color: #5B6B5B !important;
}

.mermaid-wrap {
  background: #FCFCFC;
  border: 1px dashed #D7D7D7;
  border-radius: 18px;
  padding: 12px;
  overflow: auto;
  max-height: 420px;
}

/* Simple confetti effect container */
.celebrate {
  position: relative;
  height: 64px;
  overflow: hidden;
  margin: 6px 0 10px;
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(123,174,127,0.14), rgba(123,174,127,0.00));
}
.celebrate i {
  position: absolute;
  top: -12px;
  width: 10px;
  height: 18px;
  opacity: 0.9;
  transform: rotate(25deg);
  animation: fall 900ms linear infinite;
}
@keyframes fall {
  to { transform: translateY(90px) rotate(220deg); opacity: 0.2; }
}

/* Slay-the-Spire style path map */
.sts-map {
  font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  color: #374151;
  border-radius: 18px;
  border: 1px solid #E2E8CE;
  background: #FAFAF7;
  padding: 12px;
  overflow-x: auto;
}
.sts-map .sts-legend {
  font-size: 0.82rem;
  color: #6B7280;
  margin-bottom: 10px;
}

/* Disable textarea resize handles (no draggable corners) */
.gradio-container textarea,
div[class^="gradio-container-"] textarea {
  resize: none !important;
}

/* Hide textarea internal scrollbars (no up/down arrows) */
.gradio-container textarea,
div[class^="gradio-container-"] textarea {
  overflow-y: hidden !important;
  scrollbar-width: none !important; /* Firefox */
}
.gradio-container textarea::-webkit-scrollbar,
div[class^="gradio-container-"] textarea::-webkit-scrollbar {
  width: 0 !important;
  height: 0 !important;
}
.gradio-container textarea::-webkit-scrollbar-thumb,
div[class^="gradio-container-"] textarea::-webkit-scrollbar-thumb {
  background: transparent !important;
}
.gradio-container textarea::-webkit-scrollbar-track,
div[class^="gradio-container-"] textarea::-webkit-scrollbar-track {
  background: transparent !important;
}

/* Give textareas enough height to avoid internal scrolling */
.gradio-container textarea,
div[class^="gradio-container-"] textarea {
  min-height: 72px !important;
}

/* ===== Landing page ===== */

/* Hero wrapper */
.landing-hero {
  background: radial-gradient(ellipse at 50% 30%, rgba(123,174,127,0.12) 0%, rgba(253,252,248,0) 70%);
  padding: 48px 20px 10px;
  text-align: center;
}
.landing-hero .hero-title {
  font-size: 2.8rem !important;
  letter-spacing: -1.5px !important;
  background: linear-gradient(135deg, #4A634D 0%, #7BAE7F 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.landing-subtitle {
  color: #6B7B6B !important;
  font-size: 1.1rem !important;
  margin-top: 4px;
}

/* Animated SVG container */
.landing-anim-wrap {
  display: flex;
  justify-content: center;
  margin: 8px auto 0;
  max-width: 960px;
}

/* Edge draw animation */
@keyframes edgeDraw {
  from { stroke-dashoffset: 300; }
  to   { stroke-dashoffset: 0; }
}
/* Node pop-in */
@keyframes nodePop {
  0%   { transform: scale(0); opacity: 0; }
  60%  { transform: scale(1.15); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}
/* Gentle float */
@keyframes nodeFloat {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-4px); }
}
/* Central pulse */
@keyframes corePulse {
  0%, 100% { r: 28; opacity: 0.18; }
  50%      { r: 36; opacity: 0.08; }
}
/* Label fade */
@keyframes labelFade {
  from { opacity: 0; }
  to   { opacity: 1; }
}

.landing-edge {
  stroke-dasharray: 300;
  stroke-dashoffset: 300;
  animation: edgeDraw 1.2s ease-out forwards;
}
.landing-node {
  transform-box: fill-box;
  transform-origin: center;
  transform: scale(0);
  opacity: 0;
  animation: nodePop 0.5s cubic-bezier(.34,1.56,.64,1) forwards;
}
.landing-node.landing-node-float {
  animation: nodePop 0.5s cubic-bezier(.34,1.56,.64,1) forwards,
             nodeFloat 3s ease-in-out infinite 2.8s;
}
.landing-label {
  opacity: 0;
  animation: labelFade 0.4s ease forwards;
}
/* Tagline typing */
@keyframes taglineFade {
  0%   { opacity: 0; transform: translateY(8px); }
  100% { opacity: 1; transform: translateY(0); }
}
.landing-tagline {
  text-align: center;
  font-size: 0.92rem;
  color: #9AA39A;
  margin: 10px 0 6px;
  animation: taglineFade 1s ease 3.2s both;
}

/* Leaf divider */
.landing-divider {
  text-align: center;
  font-size: 1.1rem;
  color: #B8CFB4;
  letter-spacing: 6px;
  margin: 8px 0 14px;
}

/* Card accent upgrades */
.landing-card {
  background: white !important;
  border-radius: 24px !important;
  border: 1px solid #EAE7D6 !important;
  border-left: 4px solid #7BAE7F !important;
  box-shadow: 0 8px 20px rgba(123,174,127,0.07) !important;
  padding: 22px 20px !important;
  transition: transform 0.2s, box-shadow 0.2s;
}
.landing-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 14px 28px rgba(123,174,127,0.13) !important;
}
.landing-card-icon {
  font-size: 2.2rem;
  margin-bottom: 6px;
  display: block;
}
.landing-card-title {
  font-size: 1.15rem;
  font-weight: 800;
  color: #3D5340;
  margin-bottom: 4px;
}
.landing-card-desc {
  font-size: 0.88rem;
  color: #6B7B6B;
  line-height: 1.5;
}

/* Footer */
.landing-footer {
  text-align: center;
  padding: 18px 0 8px;
  font-size: 0.8rem;
  color: #B0B8A8;
  letter-spacing: 0.5px;
}

#landing-network {
  position: relative !important;
  margin-top: -30px !important;
}
#landing-start-btn {
  position: absolute !important;
  top: 50% !important;
  left: 50% !important;
  transform: translate(-50%, -50%) !important;
  z-index: 20 !important;
  width: auto !important;
  max-width: none !important;
  padding: 0 !important;
}
#landing-start-btn button {
  font-size: 1.1rem !important;
  min-height: 52px !important;
  padding: 0.9rem 1.8rem !important;
}

.muted-hint {
  color: #6B7B6B !important;
  font-size: 0.92rem !important;
  line-height: 1.5 !important;
}
"""

# In iframes (e.g. ModelScope studio), `100vh`/`100dvh` follow the *top-level* viewport, not the iframe,
# which blows up min-heights and flex layout and can look like endless loading.
CUSTOM_CSS_IFRAME = r"""
.gradio-container,
div[class^="gradio-container-"] {
  overflow-x: clip !important;
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}
div[class^="gradio-container-"] .wrap,
div[class^="gradio-container-"] main.contain {
  min-height: auto !important;
  height: auto !important;
}
#project_right_panel,
#project_left_panel {
  height: auto !important;
  min-height: 0 !important;
  max-height: 720px !important;
  overflow-x: hidden !important;
  overflow-y: auto !important;
}
"""


def _iframe_safe_layout() -> bool:
    """Avoid vh/flex layout bugs when Gradio runs inside an iframe (ModelScope 创空间等)."""
    raw = (os.getenv("GRADIO_IFRAME_SAFE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    cwd = Path.cwd().resolve().as_posix()
    return "/studio_service/" in cwd


def _show_page(page_name: str) -> tuple[gr.Update, gr.Update]:
    return (
        gr.update(visible=page_name == "landing"),
        gr.update(visible=page_name == "project"),
    )


def _blueprint_to_mermaid(bp: CareerAcademicBlueprint) -> str:
    # Simple graph; Gradio renders as code fence, so keep small & stable.
    lines = ["flowchart TD"]
    for n in bp.nodes:
        safe_id = n.id.replace("-", "_")
        label = n.title.replace('"', "'")
        lines.append(f'  {safe_id}["{label}"]')
    for n in bp.nodes:
        for pre in n.prerequisite_ids:
            lines.append(f"  {pre} --> {n.id}")
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def _export_markdown(bp: CareerAcademicBlueprint) -> str:
    dl = bp.disciplinary_logic
    out: list[str] = []
    out.append(f"# 学习蓝图：{bp.meta.get('topic','')}".strip())
    out.append("")
    out.append("## 学科逻辑（disciplinary_logic）")
    out.append(f"- **核心问题**：{dl.core_question}")
    out.append("")
    out.append("**推理链**：")
    for i, s in enumerate(dl.reasoning_chain, 1):
        out.append(f"{i}. {s}")
    if dl.bad_orders:
        out.append("")
        out.append("**常见错序**：")
        for s in dl.bad_orders:
            out.append(f"- {s}")
    out.append("")
    out.append("## 路径图")
    out.append(_blueprint_to_mermaid(bp))
    out.append("")
    out.append("## 节点讲解与习题")
    for n in bp.nodes:
        out.append(f"### {n.title}（{n.id}）")
        out.append("")
        out.append(f"- **学什么**：{n.what_to_learn}")
        out.append(f"- **怎么学**：{n.how_to_learn}")
        out.append(f"- **如何实践**：{n.practice}")
        if n.position_in_logic:
            out.append(f"- **在主干中的作用**：{n.position_in_logic}")
        if n.prerequisite_ids:
            out.append("")
            out.append("**前置**：")
            for pid, why in zip(n.prerequisite_ids, n.why_prerequisites):
                out.append(f"- {pid} → {n.id}：{why}")
        out.append("")
        if n.teaching:
            out.append("**讲解**：")
            out.append(n.teaching.explain)
            out.append("")
            out.append("**要点**：")
            for kp in n.teaching.key_points:
                out.append(f"- {kp}")
            if n.teaching.common_pitfalls:
                out.append("")
                out.append("**易错点**：")
                for p in n.teaching.common_pitfalls:
                    out.append(f"- {p}")
        else:
            out.append("_（该节点讲解尚未生成）_")

        if n.exercises:
            out.append("")
            out.append("**练习题**：")
            for ex in n.exercises:
                out.append(f"- **[{ex.kind}] {ex.prompt}**")
                if ex.hint:
                    out.append(f"  - 提示：{ex.hint}")
                out.append(f"  - 答案要点：{ex.answer_outline}")
        out.append("")
    return "\n".join(out).strip() + "\n"


def _render_node_associations(bp: CareerAcademicBlueprint) -> str:
    lines: list[str] = []
    id_to_title = {n.id: n.title for n in bp.nodes}

    lines.append("## 跨节点关联（cross_edges）")
    if bp.cross_edges:
        for e in bp.cross_edges:
            rel = e.relation.value if hasattr(e.relation, "value") else str(e.relation)
            lines.append(f"- **{e.from_node_id}** → **{e.to_node_id}**（{rel}）：{e.why}")
    else:
        lines.append("_（当前蓝图未包含额外 cross_edges，下方为前置依赖链）_")
    lines.append("")

    lines.append("## 前置依赖（按节点）")
    has_pre = False
    for n in bp.nodes:
        if not n.prerequisite_ids:
            continue
        has_pre = True
        lines.append(f"### {n.title}（{n.id}）")
        whys = n.why_prerequisites or [""] * len(n.prerequisite_ids)
        for pid, why in zip(n.prerequisite_ids, whys):
            pt = id_to_title.get(pid, pid)
            lines.append(f"- 需先掌握 **{pid}**（{pt}）→ {why}")
        lines.append("")
    if not has_pre:
        lines.append("_（所有节点均无 prerequisite_ids）_")
        lines.append("")

    if bp.synthesis_milestones:
        lines.append("## 综合里程碑（synthesis_milestones）")
        for m in bp.synthesis_milestones:
            lines.append(f"### {m.title}")
            lines.append(f"- **涉及节点**：{', '.join(m.involved_node_ids)}")
            for d in m.deliverables:
                lines.append(f"  - 产出：{d}")
            lines.append("")

    if bp.interdiscipline_edges:
        lines.append("## 学科交叉（interdiscipline_edges）")
        for ie in bp.interdiscipline_edges:
            lines.append(
                f"- **{ie.from_ref.discipline}·{ie.from_ref.concept}** ↔ "
                f"**{ie.to_ref.discipline}·{ie.to_ref.concept}**（{ie.relation}）：{ie.mechanism}"
            )
            lines.append("")

    return "\n".join(lines).strip()


MAP_START: dict = {"current": None, "visited": []}


def _topological_layers(bp: CareerAcademicBlueprint) -> dict[str, int]:
    nodes = {n.id: n for n in bp.nodes}
    layer: dict[str, int] = {}
    for _ in range(len(nodes) + 2):
        progressed = False
        for nid, n in nodes.items():
            if nid in layer:
                continue
            if not n.prerequisite_ids:
                layer[nid] = 0
                progressed = True
                continue
            if all(p in layer for p in n.prerequisite_ids):
                layer[nid] = 1 + max(layer[p] for p in n.prerequisite_ids)
                progressed = True
        if not progressed:
            break
    for nid in nodes:
        layer.setdefault(nid, 0)
    return layer


#
# Note: Old “map/path” visualization utilities were removed.


def main() -> None:
    load_dotenv(override=False)
    # Some environments route localhost via HTTP proxy, causing Gradio startup checks to fail.
    # Ensure localhost bypasses proxy.
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    # Make missing env obvious in UI
    missing_env_hint = ""
    if not (os.getenv("BASE_URL") and os.getenv("API_KEY") and os.getenv("MODEL")):
        missing_env_hint = "⚠️ 当前未配置模型服务，生成相关功能暂不可用（仍可浏览与管理本地项目）。"

    # Soft 默认 panel 1px + primary 色、块标签 primary_100 底，易被看成「整页绿框」；压成中性描边/标签
    theme = gr.themes.Soft(primary_hue="green", radius_size="lg").set(
        border_color_primary="*neutral_300",
        border_color_primary_dark="*neutral_600",
        block_border_width="0px",
        block_border_color="*neutral_200",
        panel_border_width="0px",
        panel_border_width_dark="0px",
        panel_border_color="*neutral_200",
        block_label_background_fill="*neutral_100",
        block_title_background_fill="*neutral_100",
        block_label_text_color="*neutral_700",
        block_title_text_color="*neutral_700",
        block_label_border_width="0px",
    )
    iframe_safe = _iframe_safe_layout()
    if iframe_safe:
        print("* GRADIO_IFRAME_SAFE layout: on (iframe / 创空间)", flush=True)

    # Gradio 6：fill_height=False 时前端根 Column 的 scale 为 null，主区易被 flex 压成 0 高度（白屏仅底栏）
    # 在 iframe 内再开 fill_width 易与 100vh CSS 叠加导致左右栏被撑裂、长时间加载；创空间下关 fill_width。
    with gr.Blocks(
        title="Knowledge Forest",
        analytics_enabled=False,
        fill_height=True,
        fill_width=not iframe_safe,
    ) as demo:
        # --- Pages（顶层路由 Column；State 在页面块之后，兼容 Gradio 6 布局）---
        with gr.Column(visible=True) as landing_p:
            # ── Hero section ──
            gr.HTML(
                "<div class='landing-hero'>"
                "<div class='hero-title' style='text-align:center; padding: 32px 0 6px; font-size:2.8rem; font-weight:900;'>Knowledge Forest</div>"
                "<div class='landing-subtitle'>把厚书读薄 &nbsp;·&nbsp; 让知识像森林一样生长</div>"
                "</div>"
            )

            # ── Knowledge network + center button ──
            with gr.Column(elem_id="landing-network"):
                gr.HTML("""<div class="landing-anim-wrap" style="max-width:960px;"><svg viewBox="0 0 900 420" width="100%" height="480" xmlns="http://www.w3.org/2000/svg">
    <!-- core → tier-1 edges -->
    <line class="landing-edge" x1="450" y1="210" x2="220" y2="100" stroke="#B8CFB4" stroke-width="3" style="animation-delay:0.3s"/>
    <line class="landing-edge" x1="450" y1="210" x2="680" y2="100" stroke="#B8CFB4" stroke-width="3" style="animation-delay:0.5s"/>
    <line class="landing-edge" x1="450" y1="210" x2="220" y2="320" stroke="#B8CFB4" stroke-width="3" style="animation-delay:0.7s"/>
    <line class="landing-edge" x1="450" y1="210" x2="680" y2="320" stroke="#B8CFB4" stroke-width="3" style="animation-delay:0.9s"/>
    <!-- tier-1 → tier-2 edges -->
    <line class="landing-edge" x1="220" y1="100" x2="90"  y2="55"  stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.0s"/>
    <line class="landing-edge" x1="220" y1="100" x2="310" y2="38"  stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.1s"/>
    <line class="landing-edge" x1="220" y1="100" x2="110" y2="185" stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.15s"/>
    <line class="landing-edge" x1="680" y1="100" x2="570" y2="38"  stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.2s"/>
    <line class="landing-edge" x1="680" y1="100" x2="800" y2="60"  stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.3s"/>
    <line class="landing-edge" x1="680" y1="100" x2="790" y2="175" stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.35s"/>
    <line class="landing-edge" x1="220" y1="320" x2="90"  y2="295" stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.4s"/>
    <line class="landing-edge" x1="220" y1="320" x2="115" y2="388" stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.45s"/>
    <line class="landing-edge" x1="680" y1="320" x2="570" y2="388" stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.5s"/>
    <line class="landing-edge" x1="680" y1="320" x2="800" y2="295" stroke="#C6D9C2" stroke-width="2" style="animation-delay:1.55s"/>
    <!-- tier-2 → tier-3 edges -->
    <line class="landing-edge" x1="90"  y1="55"  x2="195" y2="28"  stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:1.7s"/>
    <line class="landing-edge" x1="310" y1="38"  x2="195" y2="28"  stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:1.75s"/>
    <line class="landing-edge" x1="110" y1="185" x2="42"  y2="235" stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:1.8s"/>
    <line class="landing-edge" x1="800" y1="60"  x2="860" y2="125" stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:1.85s"/>
    <line class="landing-edge" x1="800" y1="60"  x2="720" y2="205" stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:1.9s"/>
    <line class="landing-edge" x1="90"  y1="295" x2="42"  y2="355" stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:1.95s"/>
    <line class="landing-edge" x1="680" y1="320" x2="565" y2="315" stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:2.0s"/>
    <line class="landing-edge" x1="800" y1="295" x2="860" y2="350" stroke="#D4E4D0" stroke-width="1.5" style="animation-delay:2.02s"/>
    <!-- cross-links -->
    <line class="landing-edge" x1="570" y1="38"  x2="800" y2="60"  stroke="#DDE9DA" stroke-width="1.2" style="animation-delay:2.1s"/>
    <line class="landing-edge" x1="720" y1="205" x2="680" y2="320" stroke="#DDE9DA" stroke-width="1.2" style="animation-delay:2.15s"/>
    <line class="landing-edge" x1="790" y1="175" x2="860" y2="125" stroke="#DDE9DA" stroke-width="1.2" style="animation-delay:2.2s"/>
    <line class="landing-edge" x1="110" y1="185" x2="90"  y2="295" stroke="#DDE9DA" stroke-width="1.2" style="animation-delay:2.25s"/>
    <line class="landing-edge" x1="115" y1="388" x2="220" y2="320" stroke="#DDE9DA" stroke-width="1.2" style="animation-delay:2.3s"/>
    <line class="landing-edge" x1="42"  y1="235" x2="42"  y2="355" stroke="#DDE9DA" stroke-width="1.2" style="animation-delay:2.32s"/>
    <line class="landing-edge" x1="860" y1="125" x2="860" y2="350" stroke="#DDE9DA" stroke-width="1.2" style="animation-delay:2.35s"/>

    <!-- tier-1 nodes (4) -->
    <g class="landing-node" style="animation-delay:0.55s">
      <circle cx="220" cy="100" r="34" fill="#8FC093"/>
      <text x="220" y="106" text-anchor="middle" fill="white" font-size="16" font-weight="700" class="landing-label" style="animation-delay:0.9s">数学</text>
    </g>
    <g class="landing-node" style="animation-delay:0.7s">
      <circle cx="680" cy="100" r="34" fill="#8FC093"/>
      <text x="680" y="106" text-anchor="middle" fill="white" font-size="16" font-weight="700" class="landing-label" style="animation-delay:1.05s">编程</text>
    </g>
    <g class="landing-node" style="animation-delay:0.85s">
      <circle cx="220" cy="320" r="34" fill="#8FC093"/>
      <text x="220" y="326" text-anchor="middle" fill="white" font-size="16" font-weight="700" class="landing-label" style="animation-delay:1.2s">设计</text>
    </g>
    <g class="landing-node" style="animation-delay:1.0s">
      <circle cx="680" cy="320" r="34" fill="#8FC093"/>
      <text x="680" y="326" text-anchor="middle" fill="white" font-size="16" font-weight="700" class="landing-label" style="animation-delay:1.35s">语言</text>
    </g>

    <!-- tier-2 nodes (8) -->
    <g class="landing-node landing-node-float" style="animation-delay:1.2s">
      <circle cx="90" cy="55" r="24" fill="#A9D4AC"/>
      <text x="90" y="60" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:1.65s">线代</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.3s">
      <circle cx="310" cy="38" r="24" fill="#A9D4AC"/>
      <text x="310" y="43" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:1.7s">微积分</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.35s">
      <circle cx="110" cy="185" r="24" fill="#A9D4AC"/>
      <text x="110" y="190" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:1.75s">概率</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.4s">
      <circle cx="570" cy="38" r="24" fill="#A9D4AC"/>
      <text x="570" y="43" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:1.8s">算法</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.5s">
      <circle cx="800" cy="60" r="24" fill="#A9D4AC"/>
      <text x="800" y="65" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:1.85s">AI</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.55s">
      <circle cx="790" cy="175" r="24" fill="#A9D4AC"/>
      <text x="790" y="180" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:1.9s">Python</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.6s">
      <circle cx="90" cy="295" r="24" fill="#A9D4AC"/>
      <text x="90" y="300" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:1.95s">UI</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.65s">
      <circle cx="115" cy="388" r="24" fill="#A9D4AC"/>
      <text x="115" y="393" text-anchor="middle" fill="white" font-size="13" font-weight="600" class="landing-label" style="animation-delay:2.0s">交互</text>
    </g>

    <!-- tier-3 leaf nodes (9) -->
    <g class="landing-node landing-node-float" style="animation-delay:1.9s">
      <circle cx="195" cy="28" r="18" fill="#C4E2C6"/>
      <text x="195" y="33" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.3s">优化</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:1.95s">
      <circle cx="42" cy="235" r="18" fill="#C4E2C6"/>
      <text x="42" y="240" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.35s">统计</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:2.0s">
      <circle cx="860" cy="125" r="18" fill="#C4E2C6"/>
      <text x="860" y="130" text-anchor="middle" fill="white" font-size="10" font-weight="600" class="landing-label" style="animation-delay:2.4s">深度学习</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:2.05s">
      <circle cx="720" cy="205" r="18" fill="#C4E2C6"/>
      <text x="720" y="210" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.45s">NLP</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:2.1s">
      <circle cx="42" cy="355" r="18" fill="#C4E2C6"/>
      <text x="42" y="360" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.5s">排版</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:2.15s">
      <circle cx="570" cy="388" r="18" fill="#C4E2C6"/>
      <text x="570" y="393" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.55s">写作</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:2.2s">
      <circle cx="800" cy="295" r="18" fill="#C4E2C6"/>
      <text x="800" y="300" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.6s">翻译</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:2.25s">
      <circle cx="565" cy="315" r="18" fill="#C4E2C6"/>
      <text x="565" y="320" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.65s">语法</text>
    </g>
    <g class="landing-node landing-node-float" style="animation-delay:2.28s">
      <circle cx="860" cy="350" r="18" fill="#C4E2C6"/>
      <text x="860" y="355" text-anchor="middle" fill="white" font-size="11" font-weight="600" class="landing-label" style="animation-delay:2.68s">复盘</text>
    </g>
    </svg></div>""")
                btn_go_project = gr.Button("🌱 开始学习", variant="primary", elem_id="landing-start-btn")

            # ── Tagline ──
            gr.HTML("<div class='landing-tagline'>每一个知识点都不是孤岛 —— 让它们生长、连接、成为你的森林</div>")

            # ── Footer ──
            gr.HTML("<div class='landing-footer'>种一棵知识树，等一片森林</div>")

        with gr.Column(visible=False) as project_p:
            # 顶栏：两按钮收紧在左上，标题占剩余宽（避免 Row 三等分把按钮拉成整行半宽）
            with gr.Row(elem_id="project_top_bar"):
                with gr.Row(scale=0, elem_id="project_top_btns"):
                    btn_back_home = gr.Button("← 回到首页", elem_classes=["btn-secondary"], scale=0)
                    btn_new_project = gr.Button("创建新项目", elem_classes=["btn-primary"], scale=0)
                # Markdown 组件本身不支持 scale；用 Column 占满顶栏剩余宽度
                with gr.Column(scale=1, min_width=0):
                    project_title_display = _md("## 🌳 当前项目：未命名")

            # equal_height=False: avoid Gradio stretching the left column and injecting inner scroll
            with gr.Row(equal_height=False):
                # Left: side buttons + two-page panel
                with gr.Column(scale=1, min_width=72, elem_id="left_switch", elem_classes=["left-switch"]):
                    btn_left_chat = gr.Button("信息", elem_classes=["btn-secondary"])
                    btn_left_settings = gr.Button("附加", elem_classes=["btn-secondary"])
                    btn_left_projects = gr.Button("项目", elem_classes=["btn-secondary"])

                with gr.Column(scale=5, elem_id="project_left_panel"):
                    # Left pages stack: avoid vertical stacking; toggle visibility only
                    with gr.Column(elem_id="left_pages_stack"):
                        left_page_settings = gr.Column(visible=False, elem_classes=["left-page", "left-page-extra", "forest-card"])
                        with left_page_settings:
                            pipe_chapter = gr.Dropdown(label="章节", choices=[], interactive=True)
                            btn_pipe_3 = gr.Button("下一步", elem_classes=["btn-secondary"])
                            pipe_section = gr.Dropdown(label="小节", choices=[], interactive=True)
                            btn_pipe_4 = gr.Button("下一步", elem_classes=["btn-secondary"])
                            _md("#### 更多", elem_classes=["section-title"])
                            btn_node_assoc = gr.Button("知识关联", elem_classes=["btn-secondary"])
                            btn_export = gr.Button("导出", elem_classes=["btn-secondary"])
                            status = _md("💡 状态 空闲", elem_classes=["status-card"])
                            if missing_env_hint:
                                _md(missing_env_hint, elem_classes=["status-card"])

                        left_page_chat = gr.Column(visible=True, elem_classes=["left-page", "forest-card"])
                        with left_page_chat:
                            _md("#### 🧾 信息填写\n只用于第一次采集：填好后请点 **开始生成**。大纲、章节与讲解请切换到 **附加**。")
                            _md("##### 学科/技能", elem_classes=["section-title"])
                            intake_topic = gr.Textbox(label="学科主题（必填）", placeholder="例 高等数学 / 线性代数 / 游戏策划", container=False)
                            _md("##### 目标", elem_classes=["section-title"])
                            intake_goal = gr.Textbox(
                                label="目标（必填）",
                                placeholder="例 期末80分 / 做出一个可展示作品 / 2个月内入门并能完成项目",
                                lines=1,
                                container=False,
                            )
                            _md("##### 背景", elem_classes=["section-title"])
                            intake_background = gr.Textbox(
                                label="你的背景（必填）",
                                placeholder="例 大一 理科 高中数学一般 学过导数但不熟",
                                lines=1,
                                container=False,
                            )
                            _md("##### 时间（选填）", elem_classes=["section-title"])
                            with gr.Row():
                                intake_time = gr.Textbox(label="每周可投入时间（选填）", placeholder="例 6小时/周", container=False)
                            _md("##### 约束/偏好（选填）", elem_classes=["section-title"])
                            intake_constraints = gr.Textbox(
                                label="约束/偏好（选填）",
                                placeholder="例 只看中文 / 想要刷题为主 / 想配合项目实践",
                                lines=1,
                                container=False,
                            )
                            _md("##### 开始", elem_classes=["section-title"])
                            _md(
                                "_**提示**：首次点击「开始生成」可能需要 **5～10 分钟**（取决于网络与模型服务负载）。请耐心等待，避免重复提交。_"
                            )
                            btn_pipe_1 = gr.Button("开始生成", elem_classes=["btn-primary"])

                        left_page_projects = gr.Column(visible=False, elem_classes=["left-page", "forest-card"])
                        with left_page_projects:
                            _md("#### 切换项目")
                            sidebar_project_pick = gr.Dropdown(label="选择项目", choices=[], interactive=True)
                            btn_sidebar_reload = gr.Button("刷新列表", elem_classes=["btn-secondary"])
                            btn_sidebar_open = gr.Button("切换到该项目", elem_classes=["btn-primary"])
                            sidebar_hint = _md("_选择后点「切换」即可_")
                            sidebar_project_delete_cg = gr.CheckboxGroup(
                                label="勾选要从本机清除的项目（仅删除本机保存的数据）",
                                choices=[],
                                value=[],
                            )
                            btn_sidebar_delete = gr.Button("清除所选", elem_classes=["btn-secondary"])

                with gr.Column(scale=10, elem_classes=["forest-card"], elem_id="project_right_panel"):
                    # selected 与下方 TabItem 顺序一致：0 参考书 … 4 节点关联 5 关联漫游
                    _TAB_NODE_ASSOC = 4
                    with gr.Tabs() as project_right_tabs:
                        with gr.TabItem("📚 参考书"):
                            books_md = _md()
                        with gr.TabItem("📑 大纲"):
                            framework_md = _md()
                        with gr.TabItem("✂️ 本章小节"):
                            chapter_sections_md = _md()
                        with gr.TabItem("📝 小节详解"):
                            _md(
                                "##### 学习闭环（8 环节）\n在 **附加** 选好「小节」后点 **下一步**；下方会生成完整的 8 环节讲解。"
                            )
                            celebrate_html = gr.HTML()
                            section_expand_md = _md()
                            teen_loop_md = _md()
                            _md("##### 本小节问答（仅限本节内容）")
                            section_chat_state = gr.State(value=[])
                            section_chat = gr.Chatbot(
                                height=260,
                                latex_delimiters=MARKDOWN_LATEX_DELIMITERS,
                            )
                            section_chat_in = gr.Textbox(
                                label="提问",
                                placeholder="只问本小节：概念含义、推导直觉、例题思路、常见误区…",
                                lines=1,
                            )
                            with gr.Row():
                                btn_section_chat_send = gr.Button("发送", elem_classes=["btn-primary"])
                                btn_section_chat_clear = gr.Button("清空", elem_classes=["btn-secondary"])
                            btn_section_learned = gr.Button("已学会", elem_classes=["btn-secondary"])
                        with gr.TabItem("🔗 节点关联"):
                            _md(
                                "##### 节点关联\n"
                                "本页仅保留 **自主关联**：从已学库选两条，点「分析两者关联」生成桥梁与发散；"
                                "下方可针对该分析继续问答。"
                            )
                            learned_state = gr.State(value=[])
                            with gr.Row():
                                btn_learned_reload = gr.Button("刷新已学库", elem_classes=["btn-secondary"])
                                btn_assoc_analyze = gr.Button("分析两者关联", elem_classes=["btn-primary"])
                            with gr.Row():
                                assoc_a = gr.Dropdown(label="已学 A", choices=[], interactive=True)
                                assoc_b = gr.Dropdown(label="已学 B", choices=[], interactive=True)
                            assoc_tool_md = _md()
                            _md("---")
                            _md("##### 关联问答（针对上方关联内容提问）")
                            assoc_chat_state = gr.State(value=[])
                            assoc_chat = gr.Chatbot(
                                height=260,
                                latex_delimiters=MARKDOWN_LATEX_DELIMITERS,
                            )
                            assoc_chat_in = gr.Textbox(
                                label="提问",
                                placeholder="关于上方关联内容：为什么这两个概念相关？能否举个例子？…",
                                lines=1,
                            )
                            with gr.Row():
                                btn_assoc_chat_send = gr.Button("发送", elem_classes=["btn-primary"])
                                btn_assoc_chat_clear = gr.Button("清空", elem_classes=["btn-secondary"])
                        with gr.TabItem("🧭 关联漫游"):
                            _md(
                                "从**全局已学库**随机抽题：先 6 选 2 做关联，再反复「3 选 1」把新知识点接到上一轮结论上；"
                                "结束时生成知识网（含**传递关联**：新点与此前基础知识点之间的间接边）。"
                            )
                            roam_status_md = _md("_先点「开始漫游」。需至少 2 条已学（在「小节详解」点「已学会」）。_")
                            with gr.Row():
                                btn_roam_start = gr.Button("开始漫游", elem_classes=["btn-primary"])
                                # 回顾下拉的显隐勿绑在本按钮的「点两次」上：见 roam_replay_dd（曾用 visible=False 致 Tabs 内首帧不重排）。
                                btn_roam_finish = gr.Button("结束并生成网络", elem_classes=["btn-secondary"])
                            roam_pick_two = gr.CheckboxGroup(
                                label="随机六项中恰好选两个",
                                choices=[],
                                value=[],
                            )
                            btn_roam_confirm_two = gr.Button("确认两步关联", elem_classes=["btn-primary"])
                            roam_pick_one = gr.Radio(
                                label="三选一：与上一轮关联结论继续关联",
                                choices=[],
                                value=None,
                                info="完成首轮关联后，会显示当前「桥梁」名称，便于识别下一轮是在该桥梁上延伸。",
                            )
                            btn_roam_confirm_one = gr.Button("确认继续关联", elem_classes=["btn-primary"])
                            roam_step_md = _md("_（最新一轮关联全文）_")
                            roam_graph_md = _md("_（结束后在此查看知识网）_")
                            # 勿用 visible=False：在 Tabs 内首次 True 时前端偶发不重排，表现为「结束」要点两次才出现下拉。
                            roam_replay_dd = gr.Dropdown(
                                label="回顾本轮关联步骤",
                                info="漫游进行中为灰色不可选；点「结束并生成网络」后此处会列出各步，选一项即可看全文。",
                                choices=[],
                                value=None,
                                interactive=False,
                                visible=True,
                            )
                            roam_replay_md = _md(
                                "_（结束并生成网络后，用上方下拉框选某一步即可查看该次关联的全文。）_"
                            )


        state_bp = gr.State(value=None)
        state_map = gr.State({"current": None, "visited": []})
        state_pipeline = gr.State(
            {"student": {}, "books": None, "framework": None, "sections": {}, "teaching": {}}
        )
        state_project_id = gr.State(value=None)
        roam_state = gr.State(new_roam_state())
        # Filled by「信息」页生成；不在「附加」页重复展示表单
        topic = gr.Textbox(visible=False, value="")
        user_context = gr.Textbox(visible=False, value="")
        goal = gr.Textbox(visible=False, value="")

        def render(bp: CareerAcademicBlueprint | None):
            if bp is None:
                return ""

            dl = bp.disciplinary_logic
            logic = [
                "## 学科逻辑（disciplinary_logic）",
                f"- **核心问题**：{dl.core_question}",
                "",
                "**推理链**：",
            ]
            for i, s in enumerate(dl.reasoning_chain, 1):
                logic.append(f"{i}. {s}")
            if dl.bad_orders:
                logic.append("")
                logic.append("**常见错序**：")
                for s in dl.bad_orders:
                    logic.append(f"- {s}")

            return "\n".join(logic)

        def render_with_map(bp: CareerAcademicBlueprint | None, map_state: dict | None):
            # legacy name kept; map UI removed
            # region agent log
            _dbg_log(
                "pre-fix",
                "H1",
                "app.py:render_with_map",
                "render_with_map called",
                {"bp_is_none": bp is None, "map_state_keys": list(map_state.keys()) if isinstance(map_state, dict) else None},
            )
            # endregion agent log
            logic = render(bp)
            ms: dict = map_state if isinstance(map_state, dict) else {"current": None, "visited": []}
            # region agent log
            _dbg_log(
                "pre-fix",
                "H1",
                "app.py:render_with_map",
                "render_with_map returning 2-tuple",
                {"types": [type(logic).__name__, type(ms).__name__]},
            )
            # endregion agent log
            return logic, ms

        def _save_only(pid: str | None, pl: dict, ms: dict, bp: CareerAcademicBlueprint | None):
            return _save_current_project(project_id=pid, pipeline=pl, map_state=ms, bp=bp)

        def _chapter_dd_from_pl(pl):
            pl = _norm_pipeline(pl)
            fw = pl.get("framework")
            if not fw:
                return gr.update(choices=[], value=None)
            chs = fw.get("chapters") or []
            choices = [(f"{c.get('chapter_id')} · {c.get('title', '')}", c.get("chapter_id")) for c in chs]
            return gr.update(choices=choices, value=None)

        def _section_dd_from_pl(pl, chapter_id: str | None):
            pl = _norm_pipeline(pl)
            if not chapter_id:
                return gr.update(choices=[], value=None)
            raw = (pl.get("sections") or {}).get(chapter_id)
            if not raw:
                return gr.update(choices=[], value=None)
            cs = ChapterSectionsResult.model_validate(raw)
            choices = [(f"{s.section_id} · {s.title}", s.section_id) for s in cs.sections]
            return gr.update(choices=choices, value=None)

        def _find_chapter_obj(pl, cid: str) -> dict | None:
            fw = (pl.get("framework") or {}) if isinstance(pl, dict) else {}
            for c in fw.get("chapters") or []:
                if c.get("chapter_id") == cid:
                    return c
            return None

        def _full_bp_render(pl, ms):
            pl = _norm_pipeline(pl)
            bp = build_blueprint_from_pipeline(pl)
            mso = ms if isinstance(ms, dict) else {"current": None, "visited": []}
            if bp is None:
                # region agent log
                _dbg_log(
                    "pre-fix",
                    "H2",
                    "app.py:_full_bp_render",
                    "_full_bp_render bp is None -> returning 3-tuple",
                    {},
                )
                # endregion agent log
                return None, ""
            logic = render(bp)
            # region agent log
            _dbg_log(
                "pre-fix",
                "H2",
                "app.py:_full_bp_render",
                "_full_bp_render returning 2-tuple",
                {"bp_nodes": len(getattr(bp, "nodes", []) or []), "mso_has": list(mso.keys()) if isinstance(mso, dict) else None},
            )
            # endregion agent log
            return bp, logic

        def on_pipe_chapter_changed(pl, chid: str | None):
            pl = _norm_pipeline(pl)
            chid = (chid or "").strip() or None
            hint = "_（先在「章节」里选章，再点「下一步」生成本章要点）_"
            sec_md = hint
            if chid and (pl.get("sections") or {}).get(chid):
                sec_md = chapter_sections_to_markdown(
                    ChapterSectionsResult.model_validate(pl["sections"][chid])
                )
            elif chid:
                ch = _find_chapter_obj(pl, chid)
                if ch:
                    sec_md = (
                        f"### {chid} · {ch.get('title', '')}\n\n"
                        "_还没有本节要点 选好章节后请再点一次「下一步」_"
                    )
            detail_reset = "_（请先在「小节」里选一个，再查看本节详解）_"
            teen_reset = "_（切换/选择小节后，更好懂版本会显示在这里）_"
            return sec_md, _section_dd_from_pl(pl, chid), detail_reset, teen_reset

        def on_pipe_1_books(
            t: str,
            g: str,
            bg: str,
            time: str,
            constraints: str,
            pl: dict,
            progress: gr.Progress = gr.Progress(),
        ):
            def fail(msg: str):
                emp = gr.update(choices=[], value=None)
                return (
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    msg,
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    emp,
                    emp,
                    gr.update(),
                    {"current": None, "visited": []},
                    None,
                    "## 🌳 当前项目：未命名",
                )

            topic_v = (t or "").strip()
            goal_v = (g or "").strip()
            bg_v = (bg or "").strip()
            if not topic_v or not goal_v or not bg_v:
                return fail("🟡 请先填写 学科主题 目标 你的背景")
            extras: list[str] = []
            if (time or "").strip():
                extras.append(f"每周可投入：{time.strip()}")
            if (constraints or "").strip():
                extras.append(f"约束/偏好：{constraints.strip()}")
            user_ctx = bg_v + (("\n" + "\n".join(extras)) if extras else "")
            tb = " / ".join(extras) if extras else "（未填写）"
            cx = (constraints or "").strip() or "（无）"
            pln = _norm_pipeline(pl)
            pln["student"] = {"topic": topic_v, "user_context": user_ctx, "goal": goal_v}
            pln["books"] = None
            pln["framework"] = None
            pln["sections"] = {}
            pln["teaching"] = {}
            try:
                progress(0.02, desc="首次生成约需 5～10 分钟，请耐心等待…")
                progress(0.05, desc="正在准备内容…")
                cfg = load_config_from_env()
                cfg_parallel = load_parallel_llm_config_optional()
                first_passes = first_submit_passes_from_env()
                overlap = first_submit_overlap_enabled()
                books_user = user_prompt(
                    "books_recommend",
                    topic=topic_v,
                    user_context=user_ctx,
                    goal=goal_v,
                    time_budget=tb,
                    constraints=cx,
                )

                if first_passes >= 2 and overlap:
                    progress(0.08, desc="书单第 1 轮…")
                    b1 = chat_json(
                        cfg=cfg,
                        system=system_prompt("books_recommend"),
                        user=books_user,
                        schema_model=BooksRecommendResult,
                    )
                    books_json_b1 = json.dumps(b1.model_dump(), ensure_ascii=False)

                    cfg_books = cfg_parallel if cfg_parallel is not None else cfg
                    cfg_outline = cfg

                    def _books_refine() -> BooksRecommendResult:
                        return chat_json_multi_continue(
                            cfg=cfg_books,
                            system=system_prompt("books_recommend"),
                            user=books_user,
                            schema_model=BooksRecommendResult,
                            draft=b1,
                            passes=first_passes,
                        )

                    fw_user_b1 = user_prompt(
                        "framework_chapters",
                        topic=pln["student"].get("topic", ""),
                        user_context=pln["student"].get("user_context", ""),
                        goal=pln["student"].get("goal", ""),
                        books_json=books_json_b1,
                    )

                    def _framework_from_b1() -> ChapterFrameworkResult:
                        return chat_json_multi(
                            cfg=cfg_outline,
                            system=system_prompt("framework_chapters"),
                            user=fw_user_b1,
                            schema_model=ChapterFrameworkResult,
                            passes=first_passes,
                        )

                    if cfg_parallel is not None:
                        progress(0.2, desc="并行：书单走第二通道 + 大纲走主通道…")
                    else:
                        progress(0.2, desc="正在生成书单与大纲（并行处理）…")
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        fut_b = pool.submit(_books_refine)
                        fut_f = pool.submit(_framework_from_b1)
                        br = fut_b.result()
                        fw = fut_f.result()

                    if json.dumps(br.model_dump(), sort_keys=True) != json.dumps(b1.model_dump(), sort_keys=True):
                        progress(0.88, desc="对齐大纲与最终书单…")
                        fw = _realign_framework_to_books(cfg, fw, br)
                else:
                    br = chat_json_multi(
                        cfg=cfg,
                        system=system_prompt("books_recommend"),
                        user=books_user,
                        schema_model=BooksRecommendResult,
                        passes=first_passes,
                    )
                    pln["books"] = br.model_dump()
                    progress(0.45, desc="正在生成大纲…")
                    fw = chat_json_multi(
                        cfg=cfg,
                        system=system_prompt("framework_chapters"),
                        user=user_prompt(
                            "framework_chapters",
                            topic=pln["student"].get("topic", ""),
                            user_context=pln["student"].get("user_context", ""),
                            goal=pln["student"].get("goal", ""),
                            books_json=json.dumps(pln["books"], ensure_ascii=False),
                        ),
                        schema_model=ChapterFrameworkResult,
                        passes=first_passes,
                    )

                pln["books"] = br.model_dump()
                pln["framework"] = fw.model_dump()
                pln["sections"] = {}
                pln["teaching"] = {}

                progress(1.0, desc="完成")
                fresh = {"current": None, "visited": []}
                emp = gr.update(choices=[], value=None)
                # region agent log
                _dbg_log("pre-fix", "H2", "app.py:on_pipe_1_books", "about to unpack _full_bp_render", {"expect": 2})
                # endregion agent log
                bp, _ = _full_bp_render(pln, fresh)
                pid = _save_current_project(project_id=None, pipeline=pln, map_state=fresh, bp=bp)
                return (
                    topic_v,
                    user_ctx,
                    goal_v,
                    "✅ 已完成",
                    pln,
                    books_to_markdown(br),
                    framework_to_markdown(fw),
                    "_（选好章节后继续）_",
                    "_（选好小节后继续）_",
                    _chapter_dd_from_pl(pln),
                    emp,
                    bp,
                    fresh,
                    pid,
                    f"## 🌳 当前项目：{_project_title_from_pl(pln)}",
                )
            except Exception as e:
                progress(1.0, desc="结束")
                return fail(f"⚠️ 出错了：{e}")

        def on_pipe_2_framework(pl: dict, progress: gr.Progress = gr.Progress()):
            def fail(msg: str):
                emp = gr.update(choices=[], value=None)
                return (
                    msg,
                    gr.update(),
                    f"_{msg}_",
                    gr.update(choices=[], value=None),
                    emp,
                    None,
                    "",
                    emp,
                    "",
                    {"current": None, "visited": []},
                )

            pln = _norm_pipeline(pl)
            if not pln.get("books"):
                return fail("⚠️ 请先点「开始生成」")
            st = pln["student"]
            try:
                progress(0.08, desc="正在生成…")
                cfg = load_config_from_env()
                fw = chat_json_multi(
                    cfg=cfg,
                    system=system_prompt("framework_chapters"),
                    user=user_prompt(
                        "framework_chapters",
                        topic=st.get("topic", ""),
                        user_context=st.get("user_context", ""),
                        goal=st.get("goal", ""),
                        books_json=json.dumps(pln["books"], ensure_ascii=False),
                    ),
                    schema_model=ChapterFrameworkResult,
                    passes=2,
                )
                pln["framework"] = fw.model_dump()
                pln["sections"] = {}
                pln["teaching"] = {}
                fresh = {"current": None, "visited": []}
                # region agent log
                _dbg_log("pre-fix", "H2", "app.py:on_pipe_2_framework", "about to unpack _full_bp_render", {"expect": 2})
                # endregion agent log
                bp, _ = _full_bp_render(pln, fresh)
                progress(1.0, desc="完成")
                return (
                    "✅ 已更新，可在右侧「📑 大纲」查看",
                    pln,
                    framework_to_markdown(fw),
                    _chapter_dd_from_pl(pln),
                    gr.update(choices=[], value=None),
                    bp,
                    fresh,
                )
            except Exception as e:
                progress(1.0, desc="结束")
                return fail(f"⚠️ 出错了：{e}")

        def on_pipe_3_sections(
            pl: dict,
            chapter_id: str | None,
            pid: str | None,
            ms: dict | None,
            progress: gr.Progress = gr.Progress(),
        ):
            def fail(msg: str, plx: dict | None = None):
                px = _norm_pipeline(plx if plx is not None else pl)
                mso = ms if isinstance(ms, dict) else {"current": None, "visited": []}
                bp, _ = _full_bp_render(px, mso)
                return (
                    msg,
                    px,
                    f"_{msg}_",
                    gr.update(choices=[], value=None),
                    bp,
                    mso,
                    pid,
                )

            pln = _norm_pipeline(pl)
            cid = (chapter_id or "").strip()
            if not pln.get("framework"):
                return fail("⚠️ 请先在「信息」里点「开始生成」", pln)
            if not cid:
                return fail("⚠️ 请先在「章节」里选一个", pln)
            ch = _find_chapter_obj(pln, cid)
            if not ch:
                return fail("⚠️ 无效章节", pln)
            try:
                def _sections_quality_ok(cs: ChapterSectionsResult) -> bool:
                    # schema already enforces counts; add runtime quality gates
                    titles = [s.title.strip() for s in cs.sections if (s.title or "").strip()]
                    if len(titles) != len(cs.sections):
                        return False
                    if len(set(titles)) != len(titles):
                        return False
                    for s in cs.sections:
                        kps = [x.strip() for x in (s.knowledge_points or []) if str(x).strip()]
                        if len(kps) < 2:
                            return False
                        if len(kps) != len(set(kps)):
                            return False
                    return True

                progress(0.1, desc="正在生成（多轮优化）…")
                cfg = load_config_from_env()
                last_cs: ChapterSectionsResult | None = None
                best_cs: ChapterSectionsResult | None = None
                max_rounds = 3  # 免费 API：优先效果，多试几轮
                for r in range(1, max_rounds + 1):
                    progress(min(0.15 + 0.6 * (r / max_rounds), 0.85), desc=f"优化第 {r} 轮…")
                    last_cs = chat_json_multi(
                        cfg=cfg,
                        system=system_prompt("expand_chapter_sections"),
                        user=user_prompt(
                            "expand_chapter_sections",
                            chapter_json=json.dumps(ch, ensure_ascii=False),
                            books_json=json.dumps(pln["books"], ensure_ascii=False),
                            topic=pln["student"].get("topic", ""),
                            goal=pln["student"].get("goal", ""),
                        ),
                        schema_model=ChapterSectionsResult,
                        passes=2,
                    )
                    if _sections_quality_ok(last_cs):
                        best_cs = last_cs
                        break
                    best_cs = best_cs or last_cs

                cs_out = best_cs or last_cs  # type: ignore[assignment]
                if cs_out is None:
                    raise RuntimeError("chapter sections generation returned empty")
                if cs_out.chapter_id != cid:
                    cs_out = ChapterSectionsResult.model_validate(
                        {**cs_out.model_dump(), "chapter_id": cid}
                    )
                pln.setdefault("sections", {})
                pln["sections"][cid] = cs_out.model_dump()
                pln["teaching"] = {}
                mso = ms if isinstance(ms, dict) else {"current": None, "visited": []}
                # region agent log
                _dbg_log("pre-fix", "H2", "app.py:on_pipe_3_sections", "about to unpack _full_bp_render", {"expect": 2})
                # endregion agent log
                bp, _ = _full_bp_render(pln, mso)
                sec_md = chapter_sections_to_markdown(cs_out)
                pid2 = _save_current_project(project_id=pid, pipeline=pln, map_state=mso, bp=bp)
                progress(1.0, desc="完成")
                return (
                    "✅ 已更新",
                    pln,
                    sec_md,
                    _section_dd_from_pl(pln, cid),
                    bp,
                    mso,
                    pid2,
                )
            except Exception as e:
                progress(1.0, desc="结束")
                return fail(f"⚠️ 出错了：{e}", pln)

        _TEEN_TITLES: dict[int, str] = {
            1: "【1 旧知识引入（激活已有知识）】",
            2: "【2 核心概念（最简解释）】",
            3: "【3 可视化示例】",
            4: "【4 要点与易错】",
            5: "【5 小任务练习】",
            6: "【6 费曼检查（理解验证）】",
            7: "【7 实战演练（对齐目标）】",
            8: "【8 一句话总结】",
        }

        _TEEN_FORBIDDEN_RE = re.compile(
            r"(研究表明|数据显示|据.{0,6}统计|论文|期刊|DOI\b|http://|https://|www\.|%|某大学|哈佛|清华)",
            re.IGNORECASE,
        )

        def _cn_len(s: str) -> int:
            return len(re.sub(r"\s+", "", s or ""))

        def _teen_block_ok(s: str, *, min_len: int = 300, max_len: int = 500) -> bool:
            t = (s or "").strip()
            if not t:
                return False
            if _TEEN_FORBIDDEN_RE.search(t):
                return False
            n = _cn_len(t)
            return min_len <= n <= max_len

        def _teen_summary_ok(s: str) -> bool:
            t = (s or "").strip()
            if not t:
                return False
            if _TEEN_FORBIDDEN_RE.search(t):
                return False
            return _cn_len(t) <= 80

        def _extract_teen_blocks(text: str) -> dict[int, str]:
            """Extract blocks 1..8 from teen template output (prefer markers; fallback to headings)."""
            src = (text or "").strip()
            out: dict[int, str] = {}
            for i in range(1, 9):
                m = re.search(
                    rf"<<<?BLOCK_{i}>>>?\s*(.*?)\s*<<<?END_BLOCK_{i}>>>?",
                    src,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if m:
                    out[i] = (m.group(1) or "").strip()

            if len(out) >= 7:
                return out

            for i in range(1, 9):
                title = _TEEN_TITLES[i]
                j = i + 1
                next_title = _TEEN_TITLES[j] if j in _TEEN_TITLES else None
                if title not in src:
                    continue
                seg = src.split(title, 1)[1]
                if next_title and next_title in seg:
                    seg = seg.split(next_title, 1)[0]
                seg = re.sub(r"^-{10,}\s*$", "", seg, flags=re.MULTILINE).strip()
                out[i] = seg.strip()
            return out

        def _render_teen_blocks(blocks: dict[int, str]) -> str:
            parts: list[str] = []
            for i in range(1, 9):
                parts.append("------------------------------------------------")
                parts.append("")
                parts.append(_TEEN_TITLES[i])
                parts.append("")
                parts.append((blocks.get(i) or "").strip())
                parts.append("")
            parts.append("------------------------------------------------")
            return "\n".join(parts).strip() + "\n"

        def _gen_teen_loop_stepwise(
            *,
            cfg,
            cfg_parallel,
            pln: dict,
            sec,
            ch: dict,
            topic: str,
            goal: str,
        ) -> str:
            kpl = "\n".join(f"- {x}" for x in (getattr(sec, "knowledge_points", None) or []))
            tpl = chat_text_multi(
                cfg=cfg,
                system=system_prompt("teen_learning_loop"),
                user=user_prompt(
                    "teen_learning_loop",
                    topic=topic,
                    goal=goal,
                    user_context=str((pln.get("student") or {}).get("user_context", "") or ""),
                    section_title=str(getattr(sec, "title", "") or ""),
                    chapter_title=str(ch.get("title") or ""),
                    knowledge_points_lines=kpl or "（见本节标题）",
                    chapter_core=str(ch.get("core_ideas", "") or ""),
                    book_refs=str(ch.get("book_reference_note", "") or ""),
                ),
                temperature=0.45,
                passes=1,
            )
            blocks = _extract_teen_blocks(tpl)
            cfg_alt = cfg_parallel if cfg_parallel is not None else cfg

            def _expand_single_block(i: int) -> tuple[int, str]:
                title = _TEEN_TITLES[i]
                # 偶数环节走第二通道（若已配置），与奇数环节错开并发，减轻主网关压力
                cfg_use = cfg_alt if cfg_alt is not cfg and i % 2 == 0 else cfg
                seed = (blocks.get(i) or "").strip()
                if not seed:
                    seed = "（空）"
                best = seed
                for _r in range(1, 4):
                    feedback: list[str] = []
                    if _TEEN_FORBIDDEN_RE.search(best):
                        feedback.append("删除任何“研究/统计/论文/学校/网址/%”等外部来源痕迹。")
                    n = _cn_len(best)
                    if n < 300:
                        feedback.append("内容太短，需要补充解释与例子，目标 300~500 字。")
                    elif n > 500:
                        feedback.append("内容太长，需要压缩到 300~500 字。")

                    fb = "；".join(feedback) if feedback else "确保更具体、更贴近生活类比，且字数 300~500 字；注意短段落排版。"
                    cand = chat_text_multi(
                        cfg=cfg_use,
                        system=system_prompt("teen_learning_loop_expand"),
                        user=user_prompt(
                            "teen_learning_loop_expand",
                            block_no=str(i),
                            block_title=title,
                            seed_text=seed,
                            topic=topic,
                            goal=goal,
                            user_context=str((pln.get("student") or {}).get("user_context", "") or ""),
                            chapter_title=str(ch.get("title") or ""),
                            section_title=str(getattr(sec, "title", "") or ""),
                            knowledge_points_lines=kpl or "（见本节标题）",
                            chapter_core=str(ch.get("core_ideas", "") or ""),
                            extra_require=fb,
                        ),
                        temperature=0.45,
                        passes=1,
                    ).strip()
                    if cand:
                        best = cand
                    if _teen_block_ok(best):
                        break
                return i, best.strip()

            raw_w = (os.getenv("TEEN_EXPAND_MAX_WORKERS") or "7").strip()
            try:
                max_workers = int(raw_w)
            except ValueError:
                max_workers = 7
            max_workers = max(1, min(max_workers, 32))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for i, text in pool.map(_expand_single_block, range(1, 8)):
                    blocks[i] = text

            # Summary (block 8): keep short and safe
            s8 = (blocks.get(8) or "").strip()
            if not _teen_summary_ok(s8):
                s8 = re.split(r"[。\n]", s8)[0].strip()
                if not _teen_summary_ok(s8):
                    s8 = "用一句话说：把这个概念当作一个简单的“规则”，遇到题先按它来判断下一步怎么做。"
            blocks[8] = s8
            return _render_teen_blocks(blocks)

        def on_pipe_4_teaching(
            pl: dict,
            section_id: str | None,
            pid: str | None,
            ms: dict | None,
            progress: gr.Progress = gr.Progress(),
        ):
            def fail(msg: str, plx: dict | None = None):
                px = _norm_pipeline(plx if plx is not None else pl)
                mso = ms if isinstance(ms, dict) else {"current": None, "visited": []}
                bp, _ = _full_bp_render(px, mso)
                return (
                    msg,
                    px,
                    f"_{msg}_",
                    "",
                    bp,
                    mso,
                    pid,
                )

            pln = _norm_pipeline(pl)
            sid = (section_id or "").strip()
            if not sid:
                return fail("⚠️ 请先在「小节」里选一个", pln)
            found = None
            for cid, raw in (pln.get("sections") or {}).items():
                cs = ChapterSectionsResult.model_validate(raw)
                for s in cs.sections:
                    if s.section_id == sid:
                        found = (cid, s, _find_chapter_obj(pln, cid))
                        break
                if found:
                    break
            if not found:
                return fail("⚠️ 找不到该小节 请先选好章节并多点一次「下一步」", pln)
            cid, sec, ch = found
            if ch is None:
                return fail("⚠️ 章节数据缺失", pln)
            kpl = "\n".join(f"- {x}" for x in sec.knowledge_points)
            try:
                progress(0.05, desc="正在生成 8 环节讲解（分段多轮扩展）…")
                cfg = load_config_from_env()
                teen_md = _gen_teen_loop_stepwise(
                    cfg=cfg,
                    cfg_parallel=load_parallel_llm_config_optional(),
                    pln=pln,
                    sec=sec,
                    ch=ch,
                    topic=str(pln["student"].get("topic", "") or ""),
                    goal=str(pln["student"].get("goal", "") or ""),
                )
                pln.setdefault("teaching", {})
                pln.setdefault("teen_loop", {})
                pln["teaching"][sid] = teen_md
                pln["teen_loop"][sid] = teen_md

                mso = ms if isinstance(ms, dict) else {"current": None, "visited": []}
                bp, _ = _full_bp_render(pln, mso)

                visited_set = set(mso.get("visited") or [])
                visited_set.add(sid)
                new_ms = {"current": sid, "visited": sorted(visited_set)}

                pid2 = _save_current_project(project_id=pid, pipeline=pln, map_state=new_ms, bp=bp)
                progress(1.0, desc="完成")
                return (
                    "✅ 已生成讲解",
                    pln,
                    teen_md,
                    "",
                    bp,
                    new_ms,
                    pid2,
                )
            except Exception as e:
                progress(1.0, desc="结束")
                return fail(f"⚠️ 出错了：{e}", pln)

        # Removed unused chat clear / bulk node teaching generator (was not wired in UI)

        def on_open_node_assoc_tab():
            """Jump to「节点关联」tab; auto keyword-matching was removed as low-signal."""
            return (
                gr.update(selected=_TAB_NODE_ASSOC),
                "💡 已切到「🔗 节点关联」：请用下方「自主关联工具」选两条已学并点「分析两者关联」。",
            )

        def _learned_dropdown_choices(items: list[dict]) -> list[tuple[str, str]]:
            out: list[tuple[str, str]] = []
            for i, it in enumerate(items):
                disc = str(it.get("discipline") or "").strip() or "未命名学科"
                title = str(it.get("title") or "").strip() or "未命名"
                ref = str(it.get("source_ref") or "").strip()
                label = f"{disc} · {title}"
                if ref:
                    label += f"（{ref}）"
                out.append((label, str(i)))
            return out

        def _pack_learned_ui(status: str, items: list[dict]) -> tuple:
            choices = _learned_dropdown_choices(items)
            if not items:
                return (
                    status,
                    items,
                    gr.update(choices=[], value=None),
                    gr.update(choices=[], value=None),
                    "_（暂无已学条目）_",
                )
            return (
                status,
                items,
                gr.update(choices=choices, value=None),
                gr.update(choices=choices, value=None),
                "_选择两条已学后点「分析两者关联」_",
            )

        def on_learned_reload():
            items = load_learned()
            st = (
                "🟡 已学库为空：请在本页「小节详解」底部点「已学会」加入本节"
                if not items
                else f"✅ 已载入已学库（{len(items)} 条）"
            )
            return _pack_learned_ui(st, items)

        def on_section_mark_learned(
            pl: dict,
            sid: str | None,
            pid: str | None,
            ms: dict | None,
            bp: CareerAcademicBlueprint | None,
        ):
            pln = _norm_pipeline(pl)
            mso = ms if isinstance(ms, dict) else {"current": None, "visited": []}

            def _out_learned(status: str, items: list[dict]) -> tuple:
                return (*_pack_learned_ui(status, items), pid)

            sid2 = (sid or "").strip()
            items0 = load_learned()
            if not sid2:
                return _out_learned("🟡 请先在「附加」里选好小节", items0)
            if not (pln.get("teaching") or {}).get(sid2):
                return _out_learned("🟡 请先生成本节讲解（点「下一步」），再点「已学会」", items0)
            if not pln.get("student"):
                return _out_learned(
                    "🟡 请先在「信息」页填写并生成内容，使项目保存到本地后，再点「已学会」。"
                    "（已学库只记录已保存项目下的条目，未保存会话不会写入。）",
                    items0,
                )

            pid2 = _save_current_project(
                project_id=pid,
                pipeline=pln,
                map_state=mso,
                bp=bp,
            )
            pid_for_ref = (pid2 or "").strip()
            if not pid_for_ref:
                return _out_learned("🟡 项目未能保存到本地，本次不会写入已学库。", load_learned())

            sec_title = sid2
            kpoints: list[str] = []
            for cid, raw in (pln.get("sections") or {}).items():
                try:
                    cs = ChapterSectionsResult.model_validate(raw)
                    for s in cs.sections:
                        if s.section_id == sid2:
                            sec_title = s.title
                            kpoints = [str(x) for x in (s.knowledge_points or [])]
                            break
                    else:
                        continue
                    break
                except Exception:
                    continue

            disc = str((pln.get("student") or {}).get("topic") or "").strip() or "未命名学科"
            summary = "；".join(kpoints)[:400] if kpoints else ""
            if not summary:
                t_raw = pln.get("teaching", {}).get(sid2)
                if isinstance(t_raw, str):
                    summary = t_raw.strip()[:400]
                elif isinstance(t_raw, dict):
                    try:
                        pay = SectionTeachingExpand.model_validate(t_raw)
                        summary = (pay.teaching.explain or "").strip()[:400]
                    except Exception:
                        summary = ""
            item = {
                "discipline": disc,
                "title": str(sec_title).strip() or sid2,
                "summary": summary or "（本节讲解）",
                "keywords": kpoints[:20] if kpoints else [],
                "source_ref": f"{pid_for_ref}:{sid2}",
            }
            if not item["keywords"]:
                item["keywords"] = [item["title"]]

            added = add_learned([item])
            items1 = load_learned()

            if added <= 0:
                msg = f"🟡 未新增已学条目（可能已在库中）：{item['title']}"
                return (*_pack_learned_ui(msg, items1), pid2)

            msg = f"✅ 已学会：{item['title']}（已学库 {len(items1)} 条）"
            return (*_pack_learned_ui(msg, items1), pid2)

        def on_assoc_analyze_two(
            idx_a: str | None,
            idx_b: str | None,
            items: list[dict],
            pid: str | None,
            pl: dict,
            ms: dict | None,
            bp: CareerAcademicBlueprint | None,
            progress: gr.Progress = gr.Progress(),
        ):
            pln = _norm_pipeline(pl)
            mso = ms if isinstance(ms, dict) else {"current": None, "visited": []}
            a = (idx_a or "").strip()
            b = (idx_b or "").strip()
            if not a or not b:
                return "🟡 请先选择已学 A 与 B", "_（未选择）_", pid
            if a == b:
                return "🟡 A 和 B 需要是两条不同的已学", "_（请选择不同条目）_", pid
            try:
                ia = int(a)
                ib = int(b)
                it_a = items[ia]
                it_b = items[ib]
            except Exception:
                return "⚠️ 已学条目索引无效，请先点「刷新已学库」", "", pid

            try:
                progress(0.08, desc="正在分析两者关联…")
                cfg = load_config_from_env()
                kw_a = "、".join([str(x) for x in (it_a.get("keywords") or [])][:12])
                kw_b = "、".join([str(x) for x in (it_b.get("keywords") or [])][:12])
                user = user_prompt(
                    "assoc_analyze",
                    item_a_title=str(it_a.get("title") or ""),
                    item_a_discipline=str(it_a.get("discipline") or ""),
                    item_a_summary=str(it_a.get("summary") or ""),
                    item_a_keywords=kw_a or "（无）",
                    item_b_title=str(it_b.get("title") or ""),
                    item_b_discipline=str(it_b.get("discipline") or ""),
                    item_b_summary=str(it_b.get("summary") or ""),
                    item_b_keywords=kw_b or "（无）",
                )
                body = chat_text_multi(cfg=cfg, system=system_prompt("assoc_analyze"), user=user, temperature=0.45, passes=2)
                try:
                    upsert_assoc_edge(
                        item_a=it_a,
                        item_b=it_b,
                        analysis_preview=body,
                        node_id_fn=learned_node_id,
                    )
                except Exception:
                    pass
                pid2 = _save_current_project(project_id=pid, pipeline=pln, map_state=mso, bp=bp)
                progress(1.0, desc="完成")
                return "✅ 已分析", body, pid2
            except LLMError as e:
                progress(1.0, desc="结束")
                return f"⚠️ 关联分析失败：{e}", "", pid
            except Exception as e:
                progress(1.0, desc="结束")
                return f"⚠️ 关联分析失败：{e}", "", pid

        def _roam_assoc_body(it_a: dict, it_b: dict, *, extra_user: str = "") -> str:
            kw_a = "、".join([str(x) for x in (it_a.get("keywords") or [])][:12])
            kw_b = "、".join([str(x) for x in (it_b.get("keywords") or [])][:12])
            u = user_prompt(
                "assoc_analyze",
                item_a_title=str(it_a.get("title") or ""),
                item_a_discipline=str(it_a.get("discipline") or ""),
                item_a_summary=str(it_a.get("summary") or ""),
                item_a_keywords=kw_a or "（无）",
                item_b_title=str(it_b.get("title") or ""),
                item_b_discipline=str(it_b.get("discipline") or ""),
                item_b_summary=str(it_b.get("summary") or ""),
                item_b_keywords=kw_b or "（无）",
            )
            ex = (extra_user or "").strip()
            if ex:
                u = u + "\n\n---\n" + ex
            cfg = load_config_from_env()
            return chat_text_multi(cfg=cfg, system=system_prompt("assoc_analyze"), user=u, temperature=0.45, passes=2)

        def _roam_choice_label(item: dict) -> str:
            d = str(item.get("discipline") or "").strip()
            t = str(item.get("title") or "").strip() or "未命名"
            s = f"{d} · {t}" if d else t
            return s[:72]

        _ROAM_PICK_ONE_LABEL_DEFAULT = "三选一：与上一轮关联结论继续关联"

        def _roam_markdown_heading_bridge(st: dict, body: str) -> str:
            vnodes = st.get("virtual_nodes") or []
            if not vnodes:
                return (body or "").strip() or "_（无）_"
            b = str(vnodes[-1].get("bridge_name") or "").strip()
            text = (body or "").strip()
            if not b:
                return text or "_（无）_"
            return f"### 桥梁：「{b}」\n\n{text}" if text else f"### 桥梁：「{b}」\n\n_（无正文）_"

        def _roam_status_append_bridge(note: str, st: dict) -> str:
            vnodes = st.get("virtual_nodes") or []
            if not vnodes:
                return note
            b = str(vnodes[-1].get("bridge_name") or "").strip()
            if not b:
                return note
            return f"{note}\n\n**当前桥梁：** 「{b}」"

        def _roam_pick_one_label(st: dict, *, has_choices: bool) -> str:
            if not has_choices:
                return _ROAM_PICK_ONE_LABEL_DEFAULT
            vnodes = st.get("virtual_nodes") or []
            if not vnodes:
                return _ROAM_PICK_ONE_LABEL_DEFAULT
            b = str(vnodes[-1].get("bridge_name") or "").strip() or "（未命名）"
            return f"三选一：选一个已学知识点，与桥梁「{b}」继续关联"

        def _roam_replay_cleared():
            """Replay dropdown stays visible but disabled until finish (avoids Tabs+visible toggle glitch)."""
            return (
                gr.update(choices=[], value=None, interactive=False, visible=True),
                "_（结束并生成网络后，用上方下拉框选某一步即可查看该次关联的全文。）_",
            )

        def _roam_replay_after_finish(st: dict) -> tuple:
            """Populate replay dropdown + first step markdown after graph is built."""
            vnodes = st.get("virtual_nodes") or []
            if not vnodes:
                return (
                    gr.update(choices=[], value=None, interactive=False, visible=True),
                    "_（本次没有可回顾的关联步骤。）_",
                )
            choices: list[tuple[str, str]] = []
            for i, vn in enumerate(vnodes):
                bid = str(vn.get("bridge_name") or vn.get("one_liner") or vn.get("id") or "").strip() or f"步骤{i + 1}"
                lab = f"第 {i + 1} 步：「{bid[:40]}」"
                choices.append((lab, str(vn.get("id") or "")))
            first = vnodes[0]
            inner = {"virtual_nodes": [first]}
            md0 = _roam_markdown_heading_bridge(inner, str(first.get("markdown") or ""))
            return (
                gr.update(choices=choices, value=str(first.get("id") or ""), interactive=True, visible=True),
                md0,
            )

        def on_roam_replay_select(sel_id: str | None, rs):
            st = copy.deepcopy(rs) if isinstance(rs, dict) else new_roam_state()
            vnodes = st.get("virtual_nodes") or []
            sid = (sel_id or "").strip()
            if not vnodes:
                return "_（无关联步骤）_"
            if not sid:
                sid = str(vnodes[0].get("id") or "")
            for vn in vnodes:
                if str(vn.get("id") or "") == sid:
                    inner = {"virtual_nodes": [vn]}
                    return _roam_markdown_heading_bridge(inner, str(vn.get("markdown") or ""))
            return "_（未找到该步）_"

        def on_roam_start():
            learned = load_learned()
            st, msg = start_roam(learned)
            if st is None:
                return (
                    new_roam_state(),
                    msg,
                    gr.update(choices=[], value=[]),
                    gr.update(choices=[], value=None, label=_ROAM_PICK_ONE_LABEL_DEFAULT),
                    "_（尚无关联步骤）_",
                    "_（尚未生成网络）_",
                ) + _roam_replay_cleared()
            choices = [(_roam_choice_label(p["item"]), p["id"]) for p in st["pool6"]]
            return (
                st,
                msg,
                gr.update(choices=choices, value=[]),
                gr.update(choices=[], value=None, label=_ROAM_PICK_ONE_LABEL_DEFAULT),
                "_（勾选两项后点「确认两步关联」）_",
                "_（结束后在此查看知识网）_",
            ) + _roam_replay_cleared()

        def on_roam_confirm_two(rs, picked, progress: gr.Progress = gr.Progress()):
            learned = load_learned()
            st = copy.deepcopy(rs) if isinstance(rs, dict) else new_roam_state()
            if (st.get("phase") or "") != PHASE_PICK_TWO:
                return (
                    st,
                    "🟡 请先点「开始漫游」。",
                    gr.update(),
                    gr.update(),
                    "_（无）_",
                    gr.update(),
                ) + _roam_replay_cleared()
            ids = list(picked or [])
            if len(ids) != 2:
                return (
                    st,
                    "🟡 请恰好勾选 **2** 个知识点。",
                    gr.update(),
                    gr.update(),
                    "_（无）_",
                    gr.update(),
                ) + _roam_replay_cleared()
            if ids[0] == ids[1]:
                return (st, "🟡 请选择两个不同的知识点。", gr.update(), gr.update(), "_（无）_", gr.update()) + _roam_replay_cleared()
            pool = {p["id"]: p["item"] for p in st.get("pool6") or []}
            if ids[0] not in pool or ids[1] not in pool:
                return (st, "🟡 选择无效，请重新开始漫游。", gr.update(), gr.update(), "_（无）_", gr.update()) + _roam_replay_cleared()
            it_a, it_b = dict(pool[ids[0]]), dict(pool[ids[1]])
            try:
                progress(0.1, desc="关联分析中…")
                body = _roam_assoc_body(it_a, it_b)
                try:
                    upsert_assoc_edge(
                        item_a=it_a,
                        item_b=it_b,
                        analysis_preview=body,
                        node_id_fn=learned_node_id,
                    )
                except Exception:
                    pass
                record_first_pair(st, ids[0], ids[1], body)
                prepare_pool3(st, learned)
                p3 = st.get("pool3") or []
                if not p3:
                    note0 = "🟡 没有可继续的「三选一」候选（已全部用过或已学库不足）。请点「结束并生成网络」。"
                    c3: list = []
                    v3 = None
                else:
                    note0 = "✅ 已完成第一步关联。" + (
                        " " + st.get("next_pool_notice", "").strip() if st.get("next_pool_notice") else ""
                    )
                    c3 = [(_roam_choice_label(p["item"]), p["id"]) for p in p3]
                    v3 = None
                note = _roam_status_append_bridge(note0, st)
                step_md = _roam_markdown_heading_bridge(st, body)
                pick_label = _roam_pick_one_label(st, has_choices=bool(c3))
                return (
                    st,
                    note,
                    gr.update(choices=[], value=[]),
                    gr.update(choices=c3, value=v3, label=pick_label),
                    step_md,
                    gr.update(),
                ) + _roam_replay_cleared()
            except LLMError as e:
                progress(1.0, desc="结束")
                return (st, f"⚠️ 关联分析失败：{e}", gr.update(), gr.update(), "_（未写入步骤）_", gr.update()) + _roam_replay_cleared()
            except Exception as e:
                progress(1.0, desc="结束")
                return (st, f"⚠️ 关联分析失败：{e}", gr.update(), gr.update(), "_（未写入步骤）_", gr.update()) + _roam_replay_cleared()

        def on_roam_confirm_one(rs, one_id: str | None, progress: gr.Progress = gr.Progress()):
            learned = load_learned()
            st = copy.deepcopy(rs) if isinstance(rs, dict) else new_roam_state()
            if (st.get("phase") or "") != PHASE_PICK_ONE:
                return (
                    st,
                    "🟡 当前不在「三选一」阶段。",
                    gr.update(),
                    gr.update(),
                    "_（无）_",
                    gr.update(),
                ) + _roam_replay_cleared()
            oid = (one_id or "").strip()
            if not oid:
                return (st, "🟡 请先选择一个候选知识点。", gr.update(), gr.update(), "_（无）_", gr.update()) + _roam_replay_cleared()
            vnodes = st.get("virtual_nodes") or []
            if not vnodes:
                return (st, "🟡 尚无上一轮结论。", gr.update(), gr.update(), "_（无）_", gr.update()) + _roam_replay_cleared()
            prev = vnodes[-1]
            prev_vid = str(prev.get("id") or "")
            z_item = None
            for p in st.get("pool3") or []:
                if p.get("id") == oid:
                    z_item = dict(p["item"])
                    break
            if z_item is None:
                return (st, "🟡 选择无效，请重新点「开始漫游」或结束。", gr.update(), gr.update(), "_（无）_", gr.update()) + _roam_replay_cleared()
            lk_roam = learned_lookup_from_list(learned)
            cluster = format_base_learned_cluster(list(prev.get("base_learned_ids") or []), lk_roam)
            syn = build_synthetic_item(str(prev.get("markdown") or ""), prev, learned=learned)
            try:
                progress(0.1, desc="延续关联分析中…")
                body = _roam_assoc_body(syn, z_item, extra_user=cluster)
                record_continue(st, prev_vid, oid, body)
                prepare_pool3(st, learned)
                p3 = st.get("pool3") or []
                if not p3:
                    note0 = "✅ 已延续一步。没有更多三选一候选，请点「结束并生成网络」。"
                    c3: list = []
                    v3 = None
                else:
                    note0 = "✅ 已延续一步。" + (
                        " " + st.get("next_pool_notice", "").strip() if st.get("next_pool_notice") else ""
                    )
                    c3 = [(_roam_choice_label(p["item"]), p["id"]) for p in p3]
                    v3 = None
                note = _roam_status_append_bridge(note0, st)
                step_md = _roam_markdown_heading_bridge(st, body)
                pick_label = _roam_pick_one_label(st, has_choices=bool(c3))
                return (
                    st,
                    note,
                    gr.update(),
                    gr.update(choices=c3, value=v3, label=pick_label),
                    step_md,
                    gr.update(),
                ) + _roam_replay_cleared()
            except LLMError as e:
                progress(1.0, desc="结束")
                return (st, f"⚠️ 关联分析失败：{e}", gr.update(), gr.update(), "_（未写入步骤）_", gr.update()) + _roam_replay_cleared()
            except Exception as e:
                progress(1.0, desc="结束")
                return (st, f"⚠️ 关联分析失败：{e}", gr.update(), gr.update(), "_（未写入步骤）_", gr.update()) + _roam_replay_cleared()

        def on_roam_finish(rs):
            st = copy.deepcopy(rs) if isinstance(rs, dict) else new_roam_state()
            finish_roam(st)
            learned = load_learned()
            lk = learned_lookup_from_list(learned)
            graph_md = graph_to_mermaid(st, lk)
            if not (st.get("virtual_nodes") or []):
                graph_md = "_（本次尚未完成任何关联步骤）_\n\n" + graph_md
            return (
                st,
                "✅ 已结束漫游；下方为本次知识网（Mermaid）。",
                gr.update(choices=[], value=[]),
                gr.update(choices=[], value=None, label=_ROAM_PICK_ONE_LABEL_DEFAULT),
                gr.update(),
                graph_md,
            ) + _roam_replay_after_finish(st)

        def _section_context_markdown(pl: dict, sid: str) -> str:
            pln = _norm_pipeline(pl)
            sid = (sid or "").strip()
            if not sid:
                return "_（尚未选择小节）_"
            # Find section title/points + chapter title
            sec_title = sid
            sec_points: list[str] = []
            ch_title = ""
            ch_core = ""
            for cid, raw in (pln.get("sections") or {}).items():
                cs = ChapterSectionsResult.model_validate(raw)
                for s in cs.sections:
                    if s.section_id == sid:
                        sec_title = s.title
                        sec_points = list(s.knowledge_points)
                        ch = _find_chapter_obj(pln, cid) or {}
                        ch_title = str(ch.get("title") or cid)
                        ch_core = str(ch.get("core_ideas") or "")
                        break
                if sec_points or sec_title != sid:
                    break
            teach_md = ""
            t_raw = (pln.get("teaching") or {}).get(sid)
            if t_raw:
                if isinstance(t_raw, str):
                    teach_md = t_raw.strip()
                else:
                    try:
                        teach_md = section_teaching_to_markdown(sec_title, SectionTeachingExpand.model_validate(t_raw))
                    except Exception:
                        teach_md = ""
            parts = [
                f"## 本节：{sec_title}",
                f"- 所属章：{ch_title or '（未知）'}",
            ]
            if ch_core:
                parts.append(f"- 本章核心：{ch_core}")
            if sec_points:
                parts.append("")
                parts.append("**本节要点**：")
                parts.extend([f"- {x}" for x in sec_points])
            if teach_md.strip():
                parts.append("")
                parts.append("**本节讲解（已生成）**：")
                parts.append(teach_md[:3500])
            return "\n".join(parts).strip()

        def _section_chat_normalize(hist: list | None) -> list[dict]:
            """
            Gradio >=4.x Chatbot value: list[dict] with keys role+content.
            Legacy persisted shape: [(user, assistant), ...].
            """
            h = hist or []
            if not h:
                return []
            if all(isinstance(x, dict) and "role" in x and "content" in x for x in h):
                return [
                    {"role": str(x.get("role") or ""), "content": str(x.get("content") or "")} for x in h
                ]
            out: list[dict] = []
            for x in h:
                if isinstance(x, dict) and "role" in x and "content" in x:
                    out.append({"role": str(x.get("role") or ""), "content": str(x.get("content") or "")})
                elif isinstance(x, (list, tuple)) and len(x) == 2:
                    out.append({"role": "user", "content": str(x[0])})
                    out.append({"role": "assistant", "content": str(x[1])})
            return out

        def _history_to_text(hist: list) -> str:
            msgs = _section_chat_normalize(hist)
            out: list[str] = []
            for m in msgs[-16:]:
                role = str(m.get("role") or "")
                c = str(m.get("content") or "")
                if role == "user":
                    out.append(f"用户：{c}")
                elif role == "assistant":
                    out.append(f"助教：{c}")
                else:
                    out.append(f"{role}：{c}")
            return "\n".join(out).strip() or "（无）"

        def on_section_chat_send(
            pl: dict,
            sid: str | None,
            hist: list | None,
            user_q: str,
            pid: str | None,
            ms: dict | None,
            bp: CareerAcademicBlueprint | None,
            progress: gr.Progress = gr.Progress(),
        ):
            q = (user_q or "").strip()
            if not q:
                return gr.update(), _section_chat_normalize(hist), "", pid, _norm_pipeline(pl)
            sid2 = (sid or "").strip()
            if not sid2:
                return "🟡 先在「附加」里选好小节", _section_chat_normalize(hist), "", pid, _norm_pipeline(pl)
            try:
                # region agent log
                _hi = hist or []
                _sample = None
                if _hi:
                    _sample = _hi[-1]
                _dbg_log(
                    "section-qa-debug",
                    "H1",
                    "app.py:on_section_chat_send:entry",
                    "section QA invoked",
                    {
                        "sid": sid2,
                        "q_len": len(q),
                        "hist_len": len(_hi),
                        "hist_last_type": type(_sample).__name__,
                        "hist_last_repr": repr(_sample)[:400],
                    },
                )
                # endregion agent log
                progress(0.08, desc="助教思考中…")
                cfg = load_config_for_qa()
                # region agent log
                _dbg_log(
                    "section-qa-debug",
                    "H5",
                    "app.py:on_section_chat_send:after_cfg",
                    "config loaded",
                    {"model_set": bool(getattr(cfg, "model", None))},
                )
                # endregion agent log
                context = _section_context_markdown(pl, sid2)
                # region agent log
                _dbg_log(
                    "section-qa-debug",
                    "H3",
                    "app.py:on_section_chat_send:context",
                    "context built",
                    {"context_len": len(context or "")},
                )
                # endregion agent log
                htext = _history_to_text(hist or [])
                ans = chat_text_multi(
                    cfg=cfg,
                    system=system_prompt("section_qa"),
                    user=user_prompt("section_qa", section_context=context, chat_history=htext, user_question=q),
                    temperature=0.35,
                    passes=2,
                )
                msgs = _section_chat_normalize(hist or [])
                msgs.append({"role": "user", "content": q})
                msgs.append({"role": "assistant", "content": ans})
                new_hist = msgs[-60:]
                # persist into pipeline chats
                pln = _norm_pipeline(pl)
                pln.setdefault("chats", {}).setdefault("section", {})[sid2] = new_hist
                pid2 = _save_current_project(
                    project_id=pid,
                    pipeline=pln,
                    map_state=ms if isinstance(ms, dict) else {"current": None, "visited": []},
                    bp=bp,
                )
                progress(1.0, desc="完成")
                # region agent log
                _dbg_log(
                    "section-qa-debug",
                    "H4",
                    "app.py:on_section_chat_send:success",
                    "section QA saved",
                    {
                        "pid": pid2,
                        "new_hist_len": len(new_hist),
                        "first_msg_keys": list(new_hist[0].keys()) if new_hist else [],
                        "runId": "post-fix",
                    },
                )
                # endregion agent log
                return "✅ 已回答（仅限本节）", new_hist, "", pid2, pln
            except LLMError as e:
                progress(1.0, desc="结束")
                # region agent log
                _dbg_log(
                    "section-qa-debug",
                    "H2",
                    "app.py:on_section_chat_send:LLMError",
                    "LLMError",
                    {"type": type(e).__name__, "msg": str(e)[:500]},
                )
                # endregion agent log
                return f"⚠️ 问答失败：{e}", _section_chat_normalize(hist), "", pid, _norm_pipeline(pl)
            except Exception as e:
                progress(1.0, desc="结束")
                # region agent log
                _dbg_log(
                    "section-qa-debug",
                    "H2",
                    "app.py:on_section_chat_send:Exception",
                    "non-LLM exception",
                    {"type": type(e).__name__, "msg": str(e)[:800]},
                )
                # endregion agent log
                return (
                    f"⚠️ 问答失败：{type(e).__name__}: {e}",
                    _section_chat_normalize(hist),
                    "",
                    pid,
                    _norm_pipeline(pl),
                )

        def on_section_chat_clear(pid: str | None, pl: dict, sid: str | None, ms: dict | None, bp: CareerAcademicBlueprint | None):
            sid2 = (sid or "").strip()
            pln = _norm_pipeline(pl)
            if sid2:
                try:
                    if isinstance(pln.get("chats"), dict):
                        sec = (pln["chats"].get("section") or {}) if isinstance(pln["chats"], dict) else {}
                        if isinstance(sec, dict):
                            sec.pop(sid2, None)
                            pln["chats"]["section"] = sec
                except Exception:
                    pass
            pid2 = _save_current_project(
                project_id=pid,
                pipeline=pln,
                map_state=ms if isinstance(ms, dict) else {"current": None, "visited": []},
                bp=bp,
            )
            return "🧹 已清空本节问答", [], "", pid2, pln

        def on_assoc_chat_send(
            pl: dict,
            assoc_tool: str,
            hist: list | None,
            user_q: str,
            pid: str | None,
            ms: dict | None,
            bp: CareerAcademicBlueprint | None,
            progress: gr.Progress = gr.Progress(),
        ):
            q = (user_q or "").strip()
            if not q:
                return gr.update(), _section_chat_normalize(hist), "", pid, _norm_pipeline(pl)
            ctx_parts: list[str] = []
            tool_s = (assoc_tool or "").strip()
            if tool_s:
                ctx_parts.append("## 自主关联分析\n" + tool_s)
            context = "\n\n".join(ctx_parts) if ctx_parts else "（暂无关联分析，请先用「分析两者关联」生成一段内容）"
            try:
                progress(0.08, desc="关联助教思考中…")
                cfg = load_config_for_qa()
                htext = _history_to_text(hist or [])
                ans = chat_text_multi(
                    cfg=cfg,
                    system=system_prompt("assoc_qa"),
                    user=user_prompt("assoc_qa", assoc_context=context, chat_history=htext, user_question=q),
                    temperature=0.35,
                    passes=2,
                )
                msgs = _section_chat_normalize(hist or [])
                msgs.append({"role": "user", "content": q})
                msgs.append({"role": "assistant", "content": ans})
                new_hist = msgs[-60:]
                pln = _norm_pipeline(pl)
                pln.setdefault("chats", {})["assoc"] = new_hist
                pid2 = _save_current_project(
                    project_id=pid,
                    pipeline=pln,
                    map_state=ms if isinstance(ms, dict) else {"current": None, "visited": []},
                    bp=bp,
                )
                progress(1.0, desc="完成")
                return "✅ 已回答", new_hist, "", pid2, pln
            except LLMError as e:
                progress(1.0, desc="结束")
                return f"⚠️ 关联问答失败：{e}", _section_chat_normalize(hist), "", pid, _norm_pipeline(pl)
            except Exception as e:
                progress(1.0, desc="结束")
                return (
                    f"⚠️ 关联问答失败：{type(e).__name__}: {e}",
                    _section_chat_normalize(hist),
                    "",
                    pid,
                    _norm_pipeline(pl),
                )

        def on_assoc_chat_clear(pid: str | None, pl: dict, ms: dict | None, bp: CareerAcademicBlueprint | None):
            pln = _norm_pipeline(pl)
            try:
                if isinstance(pln.get("chats"), dict):
                    pln["chats"].pop("assoc", None)
            except Exception:
                pass
            pid2 = _save_current_project(
                project_id=pid,
                pipeline=pln,
                map_state=ms if isinstance(ms, dict) else {"current": None, "visited": []},
                bp=bp,
            )
            return "🧹 已清空关联问答", [], "", pid2, pln

        def _section_expand_and_teen_markdown(pln: dict, sid2: str) -> tuple[str, str]:
            if not sid2:
                return "_（请先在「附加」里选好小节）_", ""
            sec_title = sid2
            for _cid, raw in (pln.get("sections") or {}).items():
                try:
                    cs = ChapterSectionsResult.model_validate(raw)
                    for s in cs.sections:
                        if s.section_id == sid2:
                            sec_title = s.title
                            break
                    else:
                        continue
                    break
                except Exception:
                    continue
            raw_teaching = (pln.get("teaching") or {}).get(sid2)
            if raw_teaching:
                if isinstance(raw_teaching, str):
                    return raw_teaching.strip(), ""
                try:
                    expand_md = section_teaching_to_markdown(
                        sec_title, SectionTeachingExpand.model_validate(raw_teaching)
                    )
                except Exception:
                    expand_md = f"_小节「{sec_title}」的讲解数据异常，请对本节再点一次「下一步」重新生成。_"
                teen_raw = (pln.get("teen_loop") or {}).get(sid2)
                teen_md = str(teen_raw).strip() if teen_raw and str(teen_raw).strip() else ""
                return expand_md, teen_md
            return "_尚未生成该小节的讲解。选好本节后点「下一步」生成。_", ""

        def on_pipe_section_changed(pl: dict, sid: str | None):
            pln = _norm_pipeline(pl)
            sid2 = (sid or "").strip()
            hist: list = []
            try:
                if sid2 and isinstance(pln.get("chats"), dict):
                    sec = pln["chats"].get("section") or {}
                    if isinstance(sec, dict):
                        hist = sec.get(sid2) or []
            except Exception:
                hist = []
            safe = _section_chat_normalize(hist[:60])
            expand_md, teen_md = _section_expand_and_teen_markdown(pln, sid2)
            return safe, safe, "", expand_md, teen_md

        def on_export(pl: dict, bp: CareerAcademicBlueprint | None):
            pln = _norm_pipeline(pl)
            parts = pipeline_export_markdown(pln).strip()
            if bp:
                parts = ((parts + "\n\n---\n\n") if parts else "") + _export_markdown(bp)
            md = (parts or "").strip()
            if not md:
                return "⚠️ 暂无可导出内容"
            title = _project_title_from_pl(pln)
            try:
                save_export(name=title or "export", markdown=md)
            except Exception:
                return "⚠️ 导出失败"
            return "✅ 导出成功（已写入本地导出目录）"

        def _confetti_html() -> str:
            cols = ["#7BAE7F", "#F4D03F", "#A7D7C5", "#E2E8CE", "#F59E0B"]
            parts = ["<div class='celebrate'>"]
            for i in range(18):
                c = cols[i % len(cols)]
                left = (i * 5.5) % 100
                dur = 650 + (i % 6) * 120
                delay = (i % 4) * 60
                parts.append(
                    f"<i style='left:{left}%;background:{c};animation-duration:{dur}ms;animation-delay:{delay}ms'></i>"
                )
            parts.append("</div>")
            return "".join(parts)

        def _project_progress_tag(pl: dict) -> str:
            pln = _norm_pipeline(pl)
            parts: list[str] = []
            if pln.get("books"):
                parts.append("参考书")
            if pln.get("framework"):
                parts.append("大纲")
            nsec = len(pln.get("sections") or {})
            if nsec:
                parts.append(f"要点×{nsec}")
            nt = len(pln.get("teaching") or {})
            if nt:
                parts.append(f"讲解×{nt}")
            return " ".join(parts) if parts else "起步"

        def _sidebar_project_choices():
            items = list_projects()
            if not items:
                return (
                    gr.update(choices=[], value=None),
                    "_暂无项目。先点「创建新项目」或在「信息」页开始生成。_",
                    gr.update(choices=[], value=[]),
                )
            choices: list[tuple[str, str]] = []
            for x in items:
                raw = load_project(x.project_id)
                pl = (raw or {}).get("pipeline") or {}
                tag = _project_progress_tag(pl)
                label = f"{x.title} · {tag}（{x.updated_at or x.project_id}）"
                choices.append((label, x.project_id))
            return (
                gr.update(choices=choices, value=None),
                f"_共 {len(items)} 个项目；最近打开过的会排在前列。可在下方多选后点「清除所选」删除本地文件。_",
                gr.update(choices=choices, value=[]),
            )

        def on_inprogress_reload_sidebar():
            return _sidebar_project_choices()

        def on_sidebar_delete_projects(selected_ids: list, cur_pid: str | None):
            ids = [str(x).strip() for x in (selected_ids or []) if str(x).strip()]
            if not ids:
                dd_u, hint_u, cg_u = _sidebar_project_choices()
                return "🟡 请先在下方勾选至少一个项目", dd_u, hint_u, cg_u, gr.update()
            deleted, n_l = delete_projects_selective(ids)
            dd_u, hint_u, cg_u = _sidebar_project_choices()
            if not deleted:
                return "🟡 没有成功删除的项目（可能文件已不存在）", dd_u, hint_u, cg_u, gr.update()
            cur = (cur_pid or "").strip()
            hit = bool(cur and cur in set(deleted))
            msg = f"✅ 已从本机删除 {len(deleted)} 个项目"
            if n_l:
                msg += f"；已学库中已移除 {n_l} 条与这些项目绑定的记录"
            if hit:
                msg += "。**当前打开的项目已被删除**，请点「创建新项目」或从上方选择其它项目后点「切换」。"
            new_pid = None if hit else cur_pid
            return msg, dd_u, hint_u, cg_u, new_pid

        def go_landing():
            return _show_page("landing")

        def go_project():
            return _show_page("project")

        # Navigation（仅首页 ↔ 工作台）
        btn_go_project.click(go_project, outputs=[landing_p, project_p])
        btn_back_home.click(go_landing, outputs=[landing_p, project_p])

        btn_node_assoc.click(
            on_open_node_assoc_tab,
            outputs=[project_right_tabs, status],
        ).then(
            _save_only, inputs=[state_project_id, state_pipeline, state_map, state_bp], outputs=[state_project_id]
        )
        btn_learned_reload.click(on_learned_reload, outputs=[status, learned_state, assoc_a, assoc_b, assoc_tool_md])
        btn_assoc_analyze.click(
            on_assoc_analyze_two,
            inputs=[assoc_a, assoc_b, learned_state, state_project_id, state_pipeline, state_map, state_bp],
            outputs=[status, assoc_tool_md, state_project_id],
            show_progress="full",
        )
        btn_export.click(on_export, inputs=[state_pipeline, state_bp], outputs=[status])

        btn_section_chat_send.click(
            on_section_chat_send,
            inputs=[
                state_pipeline,
                pipe_section,
                section_chat_state,
                section_chat_in,
                state_project_id,
                state_map,
                state_bp,
            ],
            outputs=[status, section_chat_state, section_chat_in, state_project_id, state_pipeline],
            show_progress="full",
        ).then(lambda h: h, inputs=[section_chat_state], outputs=[section_chat])
        section_chat_in.submit(
            on_section_chat_send,
            inputs=[
                state_pipeline,
                pipe_section,
                section_chat_state,
                section_chat_in,
                state_project_id,
                state_map,
                state_bp,
            ],
            outputs=[status, section_chat_state, section_chat_in, state_project_id, state_pipeline],
            show_progress="full",
        ).then(lambda h: h, inputs=[section_chat_state], outputs=[section_chat])

        btn_section_chat_clear.click(
            on_section_chat_clear,
            inputs=[state_project_id, state_pipeline, pipe_section, state_map, state_bp],
            outputs=[status, section_chat_state, section_chat_in, state_project_id, state_pipeline],
            show_progress="minimal",
        ).then(lambda h: h, inputs=[section_chat_state], outputs=[section_chat])

        btn_section_learned.click(
            on_section_mark_learned,
            inputs=[state_pipeline, pipe_section, state_project_id, state_map, state_bp],
            outputs=[status, learned_state, assoc_a, assoc_b, assoc_tool_md, state_project_id],
            show_progress="minimal",
        )

        _assoc_chat_inputs = [
            state_pipeline,
            assoc_tool_md,
            assoc_chat_state,
            assoc_chat_in,
            state_project_id,
            state_map,
            state_bp,
        ]
        _assoc_chat_outputs = [status, assoc_chat_state, assoc_chat_in, state_project_id, state_pipeline]
        btn_assoc_chat_send.click(
            on_assoc_chat_send,
            inputs=_assoc_chat_inputs,
            outputs=_assoc_chat_outputs,
            show_progress="full",
        ).then(lambda h: h, inputs=[assoc_chat_state], outputs=[assoc_chat])
        assoc_chat_in.submit(
            on_assoc_chat_send,
            inputs=_assoc_chat_inputs,
            outputs=_assoc_chat_outputs,
            show_progress="full",
        ).then(lambda h: h, inputs=[assoc_chat_state], outputs=[assoc_chat])
        btn_assoc_chat_clear.click(
            on_assoc_chat_clear,
            inputs=[state_project_id, state_pipeline, state_map, state_bp],
            outputs=[status, assoc_chat_state, assoc_chat_in, state_project_id, state_pipeline],
            show_progress="minimal",
        ).then(lambda h: h, inputs=[assoc_chat_state], outputs=[assoc_chat])

        _roam_outs = [
            roam_state,
            roam_status_md,
            roam_pick_two,
            roam_pick_one,
            roam_step_md,
            roam_graph_md,
            roam_replay_dd,
            roam_replay_md,
        ]
        btn_roam_start.click(on_roam_start, outputs=_roam_outs)
        btn_roam_confirm_two.click(
            on_roam_confirm_two,
            inputs=[roam_state, roam_pick_two],
            outputs=_roam_outs,
            show_progress="full",
        )
        btn_roam_confirm_one.click(
            on_roam_confirm_one,
            inputs=[roam_state, roam_pick_one],
            outputs=_roam_outs,
            show_progress="full",
        )
        btn_roam_finish.click(on_roam_finish, inputs=[roam_state], outputs=_roam_outs)
        roam_replay_dd.change(
            on_roam_replay_select,
            inputs=[roam_replay_dd, roam_state],
            outputs=[roam_replay_md],
            show_progress="hidden",
        )

        # 信息页：按步生成（内部仍称 pipeline）
        (
            btn_pipe_1.click(
                lambda: (
                    gr.update(interactive=False, value="生成中…"),
                    "⏳ 首次生成约需 **5～10 分钟**，请耐心等待…",
                ),
                outputs=[btn_pipe_1, status],
                show_progress="hidden",
            )
            .then(
                on_pipe_1_books,
                inputs=[
                    intake_topic,
                    intake_goal,
                    intake_background,
                    intake_time,
                    intake_constraints,
                    state_pipeline,
                ],
                outputs=[
                    topic,
                    user_context,
                    goal,
                    status,
                    state_pipeline,
                    books_md,
                    framework_md,
                    chapter_sections_md,
                    section_expand_md,
                    pipe_chapter,
                    pipe_section,
                    state_bp,
                    state_map,
                    state_project_id,
                    project_title_display,
                ],
                show_progress="full",
            )
            .then(lambda: gr.update(interactive=True, value="开始生成"), outputs=[btn_pipe_1])
            .then(lambda: (gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)), outputs=[left_page_chat, left_page_settings, left_page_projects])
        )

        def _load_project_into_workspace(pid: str | None):
            pid = (pid or "").strip()
            if not pid:
                return (
                    "🟡 请选择一个项目",
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            raw = load_project(pid)
            if not raw:
                return (
                    "⚠️ 项目不存在或已损坏",
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            pln = _norm_pipeline((raw.get("pipeline") or {}))
            ms = raw.get("map_state") if isinstance(raw.get("map_state"), dict) else {"current": None, "visited": []}
            bp_raw = raw.get("bp")
            bp = CareerAcademicBlueprint.model_validate(bp_raw) if bp_raw else build_blueprint_from_pipeline(pln)
            mss = ms if isinstance(ms, dict) else {"current": None, "visited": []}
            if bp:
                # region agent log
                _dbg_log("pre-fix", "H1", "app.py:on_inprogress_open", "about to unpack render_with_map", {"expect": 2})
                # endregion agent log
                _, mss = render_with_map(bp, ms)
            fw_md = framework_to_markdown(ChapterFrameworkResult.model_validate(pln["framework"])) if pln.get("framework") else "_（暂无）_"
            books_md_s = books_to_markdown(BooksRecommendResult.model_validate(pln["books"])) if pln.get("books") else "_（暂无）_"
            title_md = f"## 🌳 当前项目：{_project_title_from_pl(pln)}"

            # Restore right-panel content as much as possible
            ch_choices = _chapter_dd_from_pl(pln)
            chapter_sections_md_s = "_（选好章节后继续）_"
            section_expand_md_s = "_（选好小节后继续）_"
            teen_loop_md_s = ""
            sec_dd = gr.update(choices=[], value=None)
            if isinstance(ch_choices, dict) and (ch_choices.get("choices") or []):
                # best-effort: pick first chapter with generated sections
                chosen_cid = None
                for cid in (pln.get("sections") or {}).keys():
                    chosen_cid = cid
                    break
                if chosen_cid:
                    chapter_sections_md_s = chapter_sections_to_markdown(
                        ChapterSectionsResult.model_validate(pln["sections"][chosen_cid])
                    )
                    sec_dd = _section_dd_from_pl(pln, chosen_cid)

            # Restore 小节详解 / 更好懂 to match current section when possible
            sid_cur = ""
            if isinstance(ms, dict) and str(ms.get("current") or "").strip():
                sid_cur = str(ms.get("current") or "").strip()
            if sid_cur and ((pln.get("teaching") or {}).get(sid_cur) or (pln.get("teen_loop") or {}).get(sid_cur)):
                section_expand_md_s, teen_loop_md_s = _section_expand_and_teen_markdown(pln, sid_cur)
            elif pln.get("teaching"):
                try:
                    sid_last = next(reversed(pln["teaching"].keys()))
                    section_expand_md_s, teen_loop_md_s = _section_expand_and_teen_markdown(pln, sid_last)
                except Exception:
                    pass

            assoc_hist: list = []
            try:
                if isinstance(pln.get("chats"), dict):
                    assoc_hist = _section_chat_normalize(pln["chats"].get("assoc") or [])
            except Exception:
                assoc_hist = []

            return (
                "✅ 已打开",
                pln,
                bp,
                mss,
                pid,
                title_md,
                books_md_s,
                fw_md,
                chapter_sections_md_s,
                section_expand_md_s,
                teen_loop_md_s,
                ch_choices,
                sec_dd,
                assoc_hist,
                assoc_hist,
            )

        def on_inprogress_open(sel_pid, cur_pid, pl, ms, bp):
            cur = (cur_pid or "").strip()
            if cur:
                _save_current_project(project_id=cur, pipeline=pl, map_state=ms, bp=bp)
            out = _load_project_into_workspace(sel_pid)
            if out[0] == "✅ 已打开":
                touch_last_opened(str(out[4]))
            return out

        def on_create_new_project(cur_pid, pl, ms, bp):
            cur = (cur_pid or "").strip()
            if cur:
                _save_current_project(project_id=cur, pipeline=pl, map_state=ms, bp=bp)
            pl0: dict = {"student": {}, "books": None, "framework": None, "sections": {}, "teaching": {}}
            ms0 = {"current": None, "visited": []}
            tp = "_（暂无）_"
            ch0 = gr.update(choices=[], value=None)
            sec0 = gr.update(choices=[], value=None)
            return (
                pl0,
                None,
                ms0,
                None,
                "## 🌳 当前项目：未命名",
                tp,
                tp,
                tp,
                "_（选好章节后继续）_",
                "_（选好小节后继续）_",
                ch0,
                sec0,
                [],
                [],
                gr.update(value=""),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
                "✅ 已进入新建项目：请在「信息」页填写后点「开始生成」",
            )

        _new_project_outputs = [
            state_pipeline,
            state_bp,
            state_map,
            state_project_id,
            project_title_display,
            books_md,
            framework_md,
            chapter_sections_md,
            section_expand_md,
            teen_loop_md,
            pipe_chapter,
            pipe_section,
            assoc_chat_state,
            assoc_chat,
            topic,
            user_context,
            goal,
            intake_topic,
            intake_goal,
            intake_background,
            intake_time,
            intake_constraints,
            left_page_chat,
            left_page_settings,
            left_page_projects,
            status,
        ]
        btn_new_project.click(
            on_create_new_project,
            inputs=[state_project_id, state_pipeline, state_map, state_bp],
            outputs=_new_project_outputs,
            show_progress="minimal",
        )
        btn_pipe_3.click(
            on_pipe_3_sections,
            inputs=[state_pipeline, pipe_chapter, state_project_id, state_map],
            outputs=[
                status,
                state_pipeline,
                chapter_sections_md,
                pipe_section,
                state_bp,
                state_map,
                state_project_id,
            ],
            show_progress="full",
        )
        btn_pipe_4.click(
            on_pipe_4_teaching,
            inputs=[state_pipeline, pipe_section, state_project_id, state_map],
            outputs=[
                status,
                state_pipeline,
                section_expand_md,
                teen_loop_md,
                state_bp,
                state_map,
                state_project_id,
            ],
            show_progress="full",
        )
        pipe_chapter.change(
            on_pipe_chapter_changed,
            inputs=[state_pipeline, pipe_chapter],
            outputs=[chapter_sections_md, pipe_section, section_expand_md, teen_loop_md],
        ).then(_save_only, inputs=[state_project_id, state_pipeline, state_map, state_bp], outputs=[state_project_id])

        pipe_section.change(
            on_pipe_section_changed,
            inputs=[state_pipeline, pipe_section],
            outputs=[section_chat_state, section_chat, section_chat_in, section_expand_md, teen_loop_md],
            show_progress="hidden",
        ).then(_save_only, inputs=[state_project_id, state_pipeline, state_map, state_bp], outputs=[state_project_id])

        # Left-side switch buttons (toggle pages)
        def show_left_chat():
            return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

        def show_left_settings():
            return gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)

        def show_left_projects():
            return gr.update(visible=False), gr.update(visible=False), gr.update(visible=True)

        _left_pages = [left_page_chat, left_page_settings, left_page_projects]
        btn_left_chat.click(show_left_chat, outputs=_left_pages)
        btn_left_settings.click(show_left_settings, outputs=_left_pages)
        btn_left_projects.click(show_left_projects, outputs=_left_pages).then(
            on_inprogress_reload_sidebar,
            outputs=[sidebar_project_pick, sidebar_hint, sidebar_project_delete_cg],
        )
        btn_sidebar_reload.click(
            on_inprogress_reload_sidebar,
            outputs=[sidebar_project_pick, sidebar_hint, sidebar_project_delete_cg],
        )
        btn_sidebar_delete.click(
            on_sidebar_delete_projects,
            inputs=[sidebar_project_delete_cg, state_project_id],
            outputs=[status, sidebar_project_pick, sidebar_hint, sidebar_project_delete_cg, state_project_id],
            show_progress="minimal",
        )

        _sidebar_open_outputs = [
            status,
            state_pipeline,
            state_bp,
            state_map,
            state_project_id,
            project_title_display,
            books_md,
            framework_md,
            chapter_sections_md,
            section_expand_md,
            teen_loop_md,
            pipe_chapter,
            pipe_section,
            assoc_chat_state,
            assoc_chat,
        ]
        btn_sidebar_open.click(
            on_inprogress_open,
            inputs=[sidebar_project_pick, state_project_id, state_pipeline, state_map, state_bp],
            outputs=_sidebar_open_outputs,
            show_progress="minimal",
        )

    # Local Windows environments sometimes fail Gradio's localhost health check when binding 0.0.0.0.
    # For online platforms (ModelScope / Spaces), set GRADIO_SERVER_NAME=0.0.0.0.
    server_name = (os.getenv("GRADIO_SERVER_NAME") or "127.0.0.1").strip()
    preferred = int((os.getenv("PORT") or os.getenv("GRADIO_SERVER_PORT") or "7860").strip())
    server_port = _first_free_port(preferred, server_name)
    if server_port != preferred:
        print(f"* Port {preferred} busy, using {server_port} instead.", flush=True)
    maybe_warmup_llm()
    demo.queue()
    _css = CUSTOM_CSS + (CUSTOM_CSS_IFRAME if iframe_safe else "")
    demo.launch(
        server_name=server_name,
        server_port=server_port,
        theme=theme,
        css=_css,
        show_error=True,
        ssr_mode=False,
    )


if __name__ == "__main__":
    main()

