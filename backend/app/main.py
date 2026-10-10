from fastapi import FastAPI

app = FastAPI(title="Sparkwave")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
