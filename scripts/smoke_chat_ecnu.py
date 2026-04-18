"""
Minimal connectivity check for OpenAI-compatible APIs (e.g. ChatECNU).

Usage (from repo root, with .env configured):
  py scripts/smoke_chat_ecnu.py

Auth / token (华东师大): https://developer.ecnu.edu.cn/vitepress/llm/authorization.html
Chat API: https://developer.ecnu.edu.cn/vitepress/llm/api/completions.html

Uses the same env vars as the main app: BASE_URL, API_KEY, MODEL, REQUEST_TIMEOUT_S.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

    base = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("API_KEY") or "").strip()
    model = (os.getenv("MODEL") or "").strip()
    timeout_s = int(os.getenv("REQUEST_TIMEOUT_S") or "60")

    if not base or not key or not model:
        print(
            "Missing BASE_URL / API_KEY / MODEL.\n"
            "For ChatECNU, set for example:\n"
            "  BASE_URL=https://chat.ecnu.edu.cn/open/api/v1\n"
            "  API_KEY=<Bearer token from 我的令牌>\n"
            "  MODEL=ecnu-plus\n"
            "Docs: https://developer.ecnu.edu.cn/vitepress/llm/authorization.html",
            file=sys.stderr,
        )
        return 1

    client = OpenAI(base_url=base, api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with exactly the single word: ok"},
        ],
        temperature=0.0,
        timeout=timeout_s,
    )
    text = (resp.choices[0].message.content or "").strip()
    print(text[:800] if text else "(empty content)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
