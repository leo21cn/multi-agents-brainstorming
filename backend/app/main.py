from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.config_routes import router as config_router
from app.api.room_routes import router as room_router
from app.api.ws import router as ws_router

app = FastAPI(title="Sparkwave")
app.include_router(config_router)
app.include_router(room_router)
app.include_router(ws_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


_frontend = Path(__file__).resolve().parents[2] / "frontend"
if _frontend.is_dir():
    app.mount("/app", StaticFiles(directory=str(_frontend), html=True), name="app")
