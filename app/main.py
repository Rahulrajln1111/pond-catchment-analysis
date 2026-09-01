import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
 
from app.routes.contour import router as contour_router
 
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
 
app = FastAPI(
    title="Pond Catchment Analysis API",
    description="Analyze KML/KMZ contour maps to find suitable pond locations with catchment areas",
    version="1.0.0",
)
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# Register routes
app.include_router(contour_router)
 
 
@app.get("/")
async def root():
    return {
        "message": "Pond Catchment Analysis API",
        "docs": "/docs",
        "endpoint": "POST /analyzeContour",
    }
 
 
@app.get("/health")
async def health():
    return {"status": "ok"}
