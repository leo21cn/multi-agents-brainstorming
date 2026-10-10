from fastapi import FastAPI

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
