from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.config_routes import router as config_router
from app.api.room_routes import router as room_router
from app.api.ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时装入嵌入提供方工厂；工厂按次解析当前模型连接，故改动无需重启
    from app.deps import install_embedding_factory

    install_embedding_factory()
    yield


app = FastAPI(title="Sparkwave", lifespan=lifespan)
app.include_router(config_router)
app.include_router(room_router)
app.include_router(ws_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app")


_frontend = Path(__file__).resolve().parents[2] / "frontend"
if _frontend.is_dir():
    for name, sub, html in (
        ("assets", "assets", False),
        ("admin", "admin", True),
        ("room", "room", True),
        ("lobby", "lobby", True),
    ):
        d = _frontend / sub
        if d.is_dir():
            app.mount(f"/{name}", StaticFiles(directory=str(d), html=html), name=name)
    app.mount("/app", StaticFiles(directory=str(_frontend), html=True), name="app")
