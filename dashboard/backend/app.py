"""FastAPI application — Heimdall Dashboard API."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from dashboard.backend.database import engine, Base
from dashboard.backend.api.instances import router as instances_router
from dashboard.backend.api.rap import router as rap_router
from dashboard.backend.api.summary import router as summary_router
from dashboard.backend.api.scan import router as scan_router
from dashboard.backend.api.cases import router as cases_router

# Create tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Heimdall",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(instances_router, prefix="/api")
app.include_router(rap_router, prefix="/api")
app.include_router(summary_router, prefix="/api")
app.include_router(scan_router, prefix="/api")
app.include_router(cases_router, prefix="/api")

# Serve React build (production)
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
