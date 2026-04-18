from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    request_timeout_s: int = 60
    max_retries: int = 2


class LLMError(RuntimeError):
    pass


_tls = threading.local()


def _thread_local_openai(cfg: LLMConfig) -> OpenAI:
    """One OpenAI client per thread; safe for concurrent chat_text/chat_json calls."""
    key = (cfg.base_url, cfg.api_key)
    if getattr(_tls, "openai_key", None) != key:
        _tls.openai_client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
        _tls.openai_key = key
    return _tls.openai_client  # type: ignore[return-value]


def load_config_from_env() -> LLMConfig:
    base_url = (os.getenv("BASE_URL") or "").strip()
    api_key = (os.getenv("API_KEY") or "").strip()
    model = (os.getenv("MODEL") or "").strip()
    if not base_url or not api_key or not model:
        raise LLMError("Missing BASE_URL / API_KEY / MODEL in environment.")
    request_timeout_s = int(os.getenv("REQUEST_TIMEOUT_S") or "60")
    max_retries = int(os.getenv("MAX_RETRIES") or "2")
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        request_timeout_s=request_timeout_s,
        max_retries=max_retries,
    )


def load_config_for_qa() -> LLMConfig:
    """
    Multi-turn Q&A (小节问答、关联问答等)：与主通道同一 BASE_URL/API_KEY，单独指定模型。
    默认 ``ecnu-max``（华东师大文档中的主推理模型）；可用环境变量 ``QA_MODEL`` / ``LLM_QA_MODEL`` 覆盖。
    若网关非 ECNU，请把 QA_MODEL 设为该网关下合法模型名（可与 MODEL 相同）。
    """
    base = load_config_from_env()
    qa_model = (os.getenv("QA_MODEL") or os.getenv("LLM_QA_MODEL") or "ecnu-max").strip()
    if not qa_model:
        qa_model = base.model
    return replace(base, model=qa_model)


def load_parallel_llm_config_optional() -> LLMConfig | None:
    """
    Optional second OpenAI-compatible endpoint (e.g. DeepSeek) for concurrent calls,
    so overlap / teen-expand can split traffic off the primary gateway (e.g. ECNU rpm limits).

    Set all three: LLM_PARALLEL_BASE_URL, LLM_PARALLEL_API_KEY, LLM_PARALLEL_MODEL.
    Optional: LLM_PARALLEL_REQUEST_TIMEOUT_S, LLM_PARALLEL_MAX_RETRIES (fallback to main env values).
    """
    base_url = (os.getenv("LLM_PARALLEL_BASE_URL") or "").strip()
    api_key = (os.getenv("LLM_PARALLEL_API_KEY") or "").strip()
    model = (os.getenv("LLM_PARALLEL_MODEL") or "").strip()
    if not base_url or not api_key or not model:
        return None
    request_timeout_s = int(os.getenv("LLM_PARALLEL_REQUEST_TIMEOUT_S") or os.getenv("REQUEST_TIMEOUT_S") or "60")
    max_retries = int(os.getenv("LLM_PARALLEL_MAX_RETRIES") or os.getenv("MAX_RETRIES") or "2")
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        request_timeout_s=request_timeout_s,
        max_retries=max_retries,
    )


def first_submit_passes_from_env() -> int:
    """
    Multi-pass count for the first user submit (books + framework in on_pipe_1_books).
    Default 2 for quality; set FIRST_SUBMIT_PASSES=1 for faster first paint.
    """
    raw = (os.getenv("FIRST_SUBMIT_PASSES") or "2").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 2
    return max(1, min(n, 8))


def first_submit_overlap_enabled() -> bool:
    """
    When FIRST_SUBMIT_PASSES>=2, overlap「书单第 2 轮起」与「大纲生成(基于书单第 1 轮)」以降低首屏墙钟时间。
    若最终书单与第 1 轮 JSON 不同，会多一次轻量对齐调用。默认关闭以免并发触发限流。
    """
    flag = (os.getenv("FIRST_SUBMIT_OVERLAP") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def maybe_warmup_llm() -> None:
    """
    Optional cold-start mitigation: one minimal completion at process start.
    Set WARMUP_ON_START=1 and valid BASE_URL/API_KEY/MODEL. Failures are ignored.
    """
    flag = (os.getenv("WARMUP_ON_START") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return
    try:
        cfg = load_config_from_env()
        chat_text(
            cfg=cfg,
            system="Reply with exactly the single word: ok",
            user="ping",
            temperature=0.0,
        )
    except Exception:
        pass


def _extract_json(text: str) -> Any:
    """
    Best-effort: if model wraps JSON in text, try to slice.
    Prefer response_format=json_object, but keep this for safety.
    """
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("No JSON object found", text, 0)


def chat_json(*, cfg: LLMConfig, system: str, user: str, schema_model: type[T]) -> T:
    client = _thread_local_openai(cfg)

    last_err: Exception | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                timeout=cfg.request_timeout_s,
            )
            content = (resp.choices[0].message.content or "").strip()
            data = _extract_json(content)
            return schema_model.model_validate(data)
        except (json.JSONDecodeError, ValidationError, Exception) as e:
            last_err = e
            if attempt >= cfg.max_retries:
                break

            # Retry with a repair instruction
            user = (
                user
                + "\n\n"
                + "上一次输出未通过解析/校验。请只输出一个 JSON 对象，严格匹配要求字段；不要额外解释文字。"
                + f"\n错误摘要：{type(e).__name__}: {str(e)[:400]}"
            )

    raise LLMError(f"LLM call failed after retries: {type(last_err).__name__}: {last_err}")


def chat_text(
    *,
    cfg: LLMConfig,
    system: str,
    user: str,
    temperature: float = 0.45,
) -> str:
    """Open-ended completion for Markdown / prose (no JSON schema)."""
    client = _thread_local_openai(cfg)

    last_err: Exception | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                timeout=cfg.request_timeout_s,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = e
            if attempt >= cfg.max_retries:
                break
            user = (
                user
                + "\n\n请直接输出上一版要求的完整 Markdown 正文，不要道歉，不要前缀说明。"
                + f"\n错误摘要：{type(e).__name__}: {str(e)[:400]}"
            )

    raise LLMError(f"LLM text call failed after retries: {type(last_err).__name__}: {last_err}")


def chat_json_multi_continue(
    *,
    cfg: LLMConfig,
    system: str,
    user: str,
    schema_model: type[T],
    draft: T,
    passes: int,
) -> T:
    """Apply passes 2..`passes` starting from `draft` (the result of pass 1). If passes<=1, returns draft."""
    passes = max(1, int(passes))
    out = draft
    for k in range(2, passes + 1):
        prev = json.dumps(out.model_dump(), ensure_ascii=False, indent=2)
        improve_user = (
            user
            + "\n\n---\n"
            + f"你上一次的输出 JSON（第 {k-1} 轮草稿）如下：\n{prev}\n"
            + "\n请在**完全保持 schema 字段结构不变**的前提下改进：\n"
            + "- 更贴合用户目标/约束/偏好\n"
            + "- 更准确、去重、避免空泛\n"
            + "- 更可执行（更具体的理由/学习建议/边界）\n"
            + "只输出一个 JSON 对象，不要解释。"
        )
        out = chat_json(cfg=cfg, system=system, user=improve_user, schema_model=schema_model)
    return out


def chat_json_multi(
    *,
    cfg: LLMConfig,
    system: str,
    user: str,
    schema_model: type[T],
    passes: int = 2,
) -> T:
    """
    Quality-first multi-pass JSON generation.
    Pass 1: draft. Pass 2+: revise based on prior JSON.
    """
    passes = max(1, int(passes))
    out = chat_json(cfg=cfg, system=system, user=user, schema_model=schema_model)
    return chat_json_multi_continue(
        cfg=cfg,
        system=system,
        user=user,
        schema_model=schema_model,
        draft=out,
        passes=passes,
    )


def chat_text_multi(
    *,
    cfg: LLMConfig,
    system: str,
    user: str,
    temperature: float = 0.45,
    passes: int = 2,
) -> str:
    """
    Quality-first multi-pass Markdown generation.
    Pass 1: draft. Pass 2+: revise based on prior draft.
    """
    passes = max(1, int(passes))
    out = chat_text(cfg=cfg, system=system, user=user, temperature=temperature)
    for k in range(2, passes + 1):
        improve_user = (
            user
            + "\n\n---\n"
            + f"你上一次的草稿（第 {k-1} 轮）如下：\n\n{out}\n\n"
            + "请改写成更高质量版本：\n"
            + "- 结构更清晰（标题/列表/层级更好）\n"
            + "- 解释更具体，例子更贴近学生生活\n"
            + "- 去重、去套话、避免无意义的长句\n"
            + "直接输出完整 Markdown 正文，不要前缀说明。"
        )
        out = chat_text(cfg=cfg, system=system, user=improve_user, temperature=temperature)
    return out

