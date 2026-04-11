from __future__ import annotations

import os

from pydantic import BaseModel

from llm_client import chat_json, load_config_from_env


class Resp(BaseModel):
    x: str


def main() -> None:
    print("BASE_URL=", os.getenv("BASE_URL"))
    print("MODEL=", os.getenv("MODEL"))
    cfg = load_config_from_env()
    r = chat_json(
        cfg=cfg,
        system='只输出一个JSON对象 {"x":"ok"} 不要多余文字',
        user='输出 {"x":"ok"}',
        schema_model=Resp,
    )
    print("OK:", r.model_dump())


if __name__ == "__main__":
    main()

