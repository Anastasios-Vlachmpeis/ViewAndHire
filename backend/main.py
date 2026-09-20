from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.db import init_db
from backend.routers import interviews, listings

init_db()
interviews.recover_interrupted_jobs()

app = FastAPI(title="ViewAndHire Mock Interview", version="1.0.0")
app.include_router(listings.router)
app.include_router(interviews.router)

frontend = settings.frontend_dir
app.mount("/css", StaticFiles(directory=frontend / "css"), name="css")
app.mount("/js", StaticFiles(directory=frontend / "js"), name="js")


@app.get("/")
def home():
    return FileResponse(frontend / "index.html")


@app.get("/listing")
def listing_page():
    return FileResponse(frontend / "listing.html")


@app.get("/settings")
def settings_page():
    return FileResponse(frontend / "settings.html")


@app.get("/session")
def session_page():
    return FileResponse(frontend / "session.html")


@app.get("/results")
def results_page():
    return FileResponse(frontend / "results.html")


@app.get("/history")
def history_page():
    return FileResponse(frontend / "history.html")


@app.get("/health")
def health():
    return {"status": "ok"}
