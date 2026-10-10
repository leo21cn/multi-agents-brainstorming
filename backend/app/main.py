from fastapi import FastAPI

from app.api.config_routes import router as config_router

app = FastAPI(title="Sparkwave")
app.include_router(config_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
