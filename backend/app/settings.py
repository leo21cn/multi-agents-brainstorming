import os
from dataclasses import dataclass
from pathlib import Path

# 默认数据目录锚定到 program/ 根，避免因工作目录不同而落到两处
_DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str
    db_path: str
    data_dir: str
    ctx_token_budget: int
    summary_trigger_tokens: int


def load_settings() -> Settings:
    data_dir = os.environ.get("SPARKWAVE_DATA_DIR", str(_DEFAULT_DATA))
    db_path = os.environ.get("SPARKWAVE_DB_PATH", str(_DEFAULT_DATA / "sparkwave.db"))
    return Settings(
        dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        db_path=db_path,
        data_dir=data_dir,
        ctx_token_budget=int(os.environ.get("SPARKWAVE_CTX_TOKEN_BUDGET", "6000")),
        summary_trigger_tokens=int(os.environ.get("SPARKWAVE_SUMMARY_TRIGGER_TOKENS", "8000")),
    )
