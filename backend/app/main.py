from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.config_routes import router as config_router
from app.api.room_routes import router as room_router
from app.api.ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时按模型连接配置嵌入提供方（真实模型或离线演示）
    try:
        from app.deps import configure_embeddings

        configure_embeddings()
    except Exception:
        pass
    yield


app = FastAPI(title="Sparkwave", lifespan=lifespan)
app.include_router(config_router)
app.include_router(room_router)
app.include_router(ws_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


_frontend = Path(__file__).resolve().parents[2] / "frontend"
if _frontend.is_dir():
    app.mount("/app", StaticFiles(directory=str(_frontend), html=True), name="app")
