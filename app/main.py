import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routes.contour import router as contour_router, get_cache_stats

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

FRONTEND_DIST = os.environ.get(
    "FRONTEND_DIST",
    # app/main.py -> app -> pond_catchment -> pond_catchment/frontend/dist
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist"),
)
HAS_FRONTEND = os.path.isdir(FRONTEND_DIST)

app = FastAPI(
    title="Pond Catchment Analysis API",
    description=(
        "Analyze terrain to find suitable pond locations with catchment "
        "areas. Phase II: draw an area on the interactive map "
        "(POST /analyzeArea) or upload contour KML (POST /analyzeContour)."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(contour_router)


@app.get("/api")
async def api_info():
    return {
        "message": "Pond Catchment Analysis API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "analyze_drawn_area": "POST /analyzeArea",
            "analyze_clicked_site": "POST /analyzeSite",
            "analyze_kml_upload": "POST /analyzeContour",
            "export_site_kml": "GET /export/kml/{context_id}/{site_index}",
            "cache_stats": "GET /cache/stats",
        },
        "cache": get_cache_stats(),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "cache": get_cache_stats()}


# ---------------------------------------------------------------------------
# Serve the React frontend (built with `npm run build` into frontend/dist).
# This enables single-deployment: one server hosts both API and UI.
# Registered LAST so API routes take priority.
# ---------------------------------------------------------------------------

if HAS_FRONTEND:
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        # Serve real files (favicon, etc.); everything else gets index.html
        candidate = os.path.normpath(os.path.join(FRONTEND_DIST, full_path))
        if full_path and candidate.startswith(FRONTEND_DIST) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))

    logger.info(f"Serving frontend from {FRONTEND_DIST}")
else:

    @app.get("/")
    async def root():
        return {"message": "Pond Catchment Analysis API", "docs": "/docs"}

    logger.info("No frontend build found — API-only mode")
