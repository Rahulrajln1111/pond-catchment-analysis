import logging
import numpy as np
from fastapi import APIRouter, File, UploadFile, HTTPException
 
from app.config import settings
from app.models import (
    AnalysisResponse,
    Coordinate,
    PondSite,
    TerrainSummary,
)
from app.parsers.kml_parser import parse_kml
from app.analysis.dem_builder import build_dem
from app.analysis.hydrology import (
    fill_sinks,
    compute_flow_direction,
    compute_flow_accumulation,
)
from app.analysis.pond_finder import (
    create_river_mask,
    find_candidate_sites,
)
 
logger = logging.getLogger(__name__)
router = APIRouter()
 
 
@router.post("/analyzeContour", response_model=AnalysisResponse)
@router.post("/findCatchment", response_model=AnalysisResponse)
async def analyze_contour(file: UploadFile = File(None), contour_map: UploadFile = File(None)):

    # --- Validate file ---
    upload = file or contour_map
    if not upload or not upload.filename:
        raise HTTPException(status_code=400, detail="No file uploaded. Send as 'file' or 'contour_map'.")
 
    ext = upload.filename.lower().rsplit(".", 1)[-1] if "." in upload.filename else ""
    if f".{ext}" not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: .{ext}. Allowed: {settings.allowed_extensions}"
        )
 
    # Read file bytes
    file_bytes = await upload.read()
    if len(file_bytes) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(file_bytes)} bytes (max: {settings.max_upload_size_bytes})"
        )
 
    try:
        
        logger.info(f"Processing uploaded file: {upload.filename}")
        parsed = parse_kml(file_bytes, upload.filename)
 
        if not parsed.contours:
            raise HTTPException(
                status_code=422,
                detail="No contour lines found in the KML/KMZ file"
            )
 
        dem_result = build_dem(parsed)
        dem = dem_result["dem"]
        transform = dem_result["transform"]
 
        filled = fill_sinks(dem)
        fdir = compute_flow_direction(filled)
        acc = compute_flow_accumulation(fdir, filled)
 
        river_contours = [c for c in parsed.contours if c.is_river]
        river_mask = create_river_mask(river_contours, transform)
 
        sites = find_candidate_sites(dem, fdir, acc, transform, river_mask)
 
        valid_elevs = dem[~np.isnan(dem)]
        elevations = [c.elevation for c in parsed.contours]
 
        terrain = TerrainSummary(
            elevation_min_m=float(np.nanmin(dem)),
            elevation_max_m=float(np.nanmax(dem)),
            elevation_range_m=float(np.nanmax(dem) - np.nanmin(dem)),
            total_contours=len(parsed.contours),
            total_points=sum(len(c.coords) for c in parsed.contours),
            area_boundary=[
                Coordinate(latitude=lat, longitude=lon)
                for lon, lat in parsed.boundary_coords
            ],
        )
 
        candidate_sites = [
            PondSite(
                location=Coordinate(
                    latitude=s["location"]["latitude"],
                    longitude=s["location"]["longitude"],
                ),
                elevation_m=s["elevation_m"],
                catchment_area_sqm=s["catchment_area_sqm"],
                catchment_area_hectares=s["catchment_area_hectares"],
                catchment_boundary=[
                    Coordinate(latitude=b["latitude"], longitude=b["longitude"])
                    for b in s["catchment_boundary"]
                ],
                river_excluded=s["river_excluded"],
                pond_boundary=[
                    Coordinate(latitude=b["latitude"], longitude=b["longitude"])
                    for b in s["pond_boundary"]
                ],
                pond_area_sqm=s["pond_area_sqm"],
                pond_volume_m3=s["pond_volume_m3"],
                pond_depth_m=s["pond_depth_m"],
                water_surface_elevation_m=s["water_surface_elevation_m"],
            )
            for s in sites
        ]
 
        response = AnalysisResponse(
            status="success",
            message=f"Found {len(candidate_sites)} candidate pond sites from {len(parsed.contours)} contour lines",
            terrain=terrain,
            candidate_sites=candidate_sites,
            rivers_detected=len(river_contours) > 0,
        )
 
        logger.info(f"Analysis complete: {len(candidate_sites)} sites returned")
        return response
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
