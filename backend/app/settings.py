import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str
    db_path: str
    data_dir: str
    ctx_token_budget: int
    summary_trigger_tokens: int


def load_settings() -> Settings:
    return Settings(
        dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        db_path=os.environ.get("SPARKWAVE_DB_PATH", "./data/sparkwave.db"),
        data_dir=os.environ.get("SPARKWAVE_DATA_DIR", "./data"),
        ctx_token_budget=int(os.environ.get("SPARKWAVE_CTX_TOKEN_BUDGET", "6000")),
        summary_trigger_tokens=int(os.environ.get("SPARKWAVE_SUMMARY_TRIGGER_TOKENS", "8000")),
    )
