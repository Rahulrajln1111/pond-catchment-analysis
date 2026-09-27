import logging
import math
import time
import uuid
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import Response
from scipy.ndimage import binary_dilation

from app.config import settings
from app.models import (
    AnalysisResponse,
    AreaAnalysisRequest,
    Coordinate,
    CustomSiteRequest,
    PondSite,
    TerrainSummary,
)
from app.parsers.kml_parser import parse_kml
from app.analysis.dem_builder import build_dem
from app.analysis.terrain_tiles import build_dem_from_polygon
from app.analysis.hydrology import (
    fill_sinks,
    compute_flow_direction,
    compute_flow_accumulation,
    detect_streams,
)
from app.analysis.osm_rivers import river_mask_from_osm
from app.analysis.pond_finder import (
    create_river_mask,
    find_candidate_sites,
    analyze_custom_site,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Terrain context cache — lets the user click / refine without re-fetching
# and re-computing the DEM for the same selection. LRU + TTL eviction.
# ---------------------------------------------------------------------------

class ContextCache:
    """In-memory LRU cache of analyzed terrain contexts."""

    def __init__(self, max_items: int, ttl_seconds: float):
        self._store: OrderedDict[str, dict] = OrderedDict()
        self._max_items = max_items
        self._ttl = ttl_seconds

    def put(self, ctx_id: str, ctx: dict) -> None:
        self._evite_expired()
        while len(self._store) >= self._max_items:
            self._store.popitem(last=False)  # evict least-recently-used
        self._store[ctx_id] = ctx

    def get(self, ctx_id: str) -> dict | None:
        ctx = self._store.get(ctx_id)
        if ctx is None:
            return None
        if time.time() - ctx["created_at"] > self._ttl:
            del self._store[ctx_id]
            return None
        self._store.move_to_end(ctx_id)  # mark as recently used
        return ctx

    def _evite_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._store.items() if now - v["created_at"] > self._ttl]
        for k in expired:
            del self._store[k]

    def stats(self) -> dict:
        self._evite_expired()
        return {"contexts_cached": len(self._store), "max": self._max_items}


_cache = ContextCache(
    max_items=settings.analysis.max_cached_contexts,
    ttl_seconds=settings.analysis.context_ttl_seconds,
)


def get_cache_stats() -> dict:
    return _cache.stats()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalize_polygon(geom: dict) -> dict:
    """
    Validate a GeoJSON Polygon/MultiPolygon and return it with coordinates
    normalized to [lon, lat] order.

    Accepts both [lon, lat] (GeoJSON standard, what Leaflet sends) and
    [lat, lon] (common mistake), auto-detected from value ranges.
    """
    gtype = geom.get("type")
    if gtype not in ("Polygon", "MultiPolygon"):
        raise HTTPException(status_code=422, detail="polygon.type must be 'Polygon' or 'MultiPolygon'")

    def norm_ring(ring) -> list[tuple[float, float]]:
        if len(ring) < 4:
            raise HTTPException(status_code=422, detail="Polygon ring must have at least 4 positions")
        # GeoJSON standard: [longitude, latitude]. No guessing.
        for p in ring:
            lon, lat = float(p[0]), float(p[1])
            if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Coordinate ({p[0]}, {p[1]}) is out of range. "
                        "Send coordinates as [longitude, latitude] (GeoJSON standard)."
                    ),
                )
        return [(float(p[0]), float(p[1])) for p in ring]

    try:
        if gtype == "Polygon":
            rings = [norm_ring(r) for r in geom["coordinates"]]
            return {"type": "Polygon", "coordinates": [[list(c) for c in r] for r in rings]}
        polys = [[[list(c) for c in norm_ring(r)] for r in poly] for poly in geom["coordinates"]]
        return {"type": "MultiPolygon", "coordinates": polys}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid polygon coordinates: {e}")


def _polygon_area_sqkm(geom: dict) -> float:
    """
    Approximate geodesic area of the polygon's outer ring(s) in sq km
    (shoelace in local metric coordinates at the ring's mean latitude).
    """
    meters_per_deg_lat = 111_320.0

    def ring_area_sqm(ring: list[tuple[float, float]]) -> float:
        lats = [p[1] for p in ring]
        m_per_lon = 111_320.0 * math.cos(math.radians(sum(lats) / len(lats)))
        pts = [(p[0] * m_per_lon, p[1] * meters_per_deg_lat) for p in ring]
        s = 0.0
        n = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2.0

    if geom["type"] == "Polygon":
        area = ring_area_sqm([(c[0], c[1]) for c in geom["coordinates"][0]])
    else:
        area = sum(
            ring_area_sqm([(c[0], c[1]) for c in poly[0]])
            for poly in geom["coordinates"]
        )
    return area / 1_000_000.0


def _run_pipeline(
    dem: np.ndarray,
    transform: dict,
    max_sites: int,
    geom: dict | None = None,
):
    """Shared hydrology + pond finding. Returns (filled, fdir, acc, river_mask, rivers, sites)."""
    filled = fill_sinks(dem)
    fdir = compute_flow_direction(filled)
    acc = compute_flow_accumulation(fdir, filled)

    # River exclusion for drawn areas. Primary source: OpenStreetMap
    # (surveyed river geometry via the free Overpass API — reliable where
    # DEM thresholding fails, e.g. flat terrain). Fallback: flow-accumulation
    # thresholding on the DEM itself when OSM is unreachable.
    river_source = "none"
    try:
        river_mask, _ways = river_mask_from_osm(geom, transform, filled)
        rivers = river_mask
        river_source = "osm"
    except Exception as e:  # noqa: BLE001 - OSM is best-effort
        logger.warning(f"OSM river lookup failed ({e}); falling back to DEM detection")
        streams = detect_streams(fdir, acc)
        river_threshold = max(
            settings.hydrology.river_min_accumulation,
            float(np.nanmax(acc)) * settings.hydrology.river_fraction_of_max,
        )
        rivers = acc >= river_threshold
        if rivers.any() and settings.hydrology.stream_buffer_cells > 0:
            rivers = binary_dilation(
                rivers, iterations=settings.hydrology.stream_buffer_cells
            )
        river_mask = rivers
        river_source = "dem-fallback"

    logger.info(
        f"River exclusion source={river_source}: {int(rivers.sum())} mask cells "
        f"({100.0 * rivers.sum() / rivers.size:.2f}% of grid)"
    )

    sites = find_candidate_sites(filled, fdir, acc, transform, river_mask)
    return filled, fdir, acc, river_mask, rivers, sites


def _sites_to_models(sites: list[dict]) -> list[PondSite]:
    return [
        PondSite(
            location=Coordinate(**s["location"]),
            elevation_m=s["elevation_m"],
            catchment_area_sqm=s["catchment_area_sqm"],
            catchment_area_hectares=s["catchment_area_hectares"],
            catchment_boundary=[
                Coordinate(latitude=b["latitude"], longitude=b["longitude"])
                for b in s["catchment_boundary"]
            ],
            river_excluded=s.get("river_excluded", False),
            pond_boundary=[
                Coordinate(latitude=b["latitude"], longitude=b["longitude"])
                for b in s.get("pond_boundary", [])
            ],
            pond_boundary_rings=[
                [
                    Coordinate(latitude=b["latitude"], longitude=b["longitude"])
                    for b in ring
                ]
                for ring in s.get("pond_boundary_rings", [])
            ],
            pond_area_sqm=s.get("pond_area_sqm", 0.0),
            pond_volume_m3=s.get("pond_volume_m3", 0.0),
            pond_depth_m=s.get("pond_depth_m", 0.0),
            water_surface_elevation_m=s.get("water_surface_elevation_m", 0.0),
        )
        for s in sites
    ]


def _new_context_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Phase I endpoint — KML/KMZ contour upload (kept for compatibility)
# ---------------------------------------------------------------------------

@router.post("/analyzeContour", response_model=AnalysisResponse)
@router.post("/findCatchment", response_model=AnalysisResponse)
async def analyze_contour(file: UploadFile = File(None), contour_map: UploadFile = File(None)):
    started = time.perf_counter()

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
            raise HTTPException(status_code=422, detail="No contour lines found in the KML/KMZ file")

        dem_result = build_dem(parsed)
        dem = dem_result["dem"]
        transform = dem_result["transform"]

        filled = fill_sinks(dem)
        fdir = compute_flow_direction(filled)
        acc = compute_flow_accumulation(fdir, filled)

        river_contours = [c for c in parsed.contours if c.is_river]
        river_mask = create_river_mask(river_contours, transform)

        sites = find_candidate_sites(filled, fdir, acc, transform, river_mask)

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
            grid_cells=int(dem.size),
            grid_resolution_m=transform["cell_size_m"],
        )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return AnalysisResponse(
            status="success",
            message=f"Found {len(sites)} candidate pond sites from {len(parsed.contours)} contour lines",
            terrain=terrain,
            candidate_sites=_sites_to_models(sites),
            rivers_detected=len(river_contours) > 0,
            data_source="kml-contours",
            analysis_time_ms=elapsed_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


# ---------------------------------------------------------------------------
# Phase II — drawn-area analysis on the interactive map
# ---------------------------------------------------------------------------

@router.post("/analyzeArea", response_model=AnalysisResponse)
@router.post("/analyzePolygon", response_model=AnalysisResponse)
async def analyze_area(request: AreaAnalysisRequest):
    """
    Analyze a land area drawn on the map (GeoJSON Polygon/MultiPolygon).

    Downloads terrain for the selection (AWS Terrain Tiles), runs the full
    hydrology + pond-siting pipeline, caches the context so the user can
    click specific points afterwards, and returns candidate sites with
    catchment boundaries and pond volumes.
    """
    started = time.perf_counter()
    geom = _normalize_polygon(request.polygon.model_dump())

    area_sqkm = _polygon_area_sqkm(geom)
    if area_sqkm < settings.analysis.min_area_sqkm:
        raise HTTPException(
            status_code=422,
            detail=f"Selected area too small ({area_sqkm:.4f} sq km). Minimum is {settings.analysis.min_area_sqkm} sq km.",
        )
    if area_sqkm > settings.analysis.max_area_sqkm:
        raise HTTPException(
            status_code=422,
            detail=f"Selected area too large ({area_sqkm:.1f} sq km). Maximum is {settings.analysis.max_area_sqkm} sq km.",
        )

    try:
        dem_result = build_dem_from_polygon(geom, target_cell_m=request.target_cell_m)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"Terrain data source unavailable: {e}")

    dem = dem_result["dem"]
    transform = dem_result["transform"]

    try:
        filled, fdir, acc, river_mask, rivers, sites = _run_pipeline(
            dem, transform, request.max_sites, geom=geom
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    # Cache the context for point-click refinement and KML export
    context_id = _new_context_id()
    _cache.put(context_id, {
        "created_at": time.time(),
        "dem": filled,
        "fdir": fdir,
        "acc": acc,
        "transform": transform,
        "river_mask": river_mask,
        "sites": sites,
        "bbox": dem_result["bbox"],
    })

    west, south, east, north = dem_result["bbox"]
    terrain = TerrainSummary(
        elevation_min_m=float(np.nanmin(dem)),
        elevation_max_m=float(np.nanmax(dem)),
        elevation_range_m=float(np.nanmax(dem) - np.nanmin(dem)),
        total_contours=0,
        total_points=0,
        area_boundary=[
            Coordinate(latitude=south, longitude=west),
            Coordinate(latitude=south, longitude=east),
            Coordinate(latitude=north, longitude=east),
            Coordinate(latitude=north, longitude=west),
            Coordinate(latitude=south, longitude=west),
        ],
        grid_cells=int(np.sum(np.isfinite(dem))),
        grid_resolution_m=transform["cell_size_m"],
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        f"analyzeArea: {area_sqkm:.2f} sq km, {transform['rows']}x{transform['cols']} grid, "
        f"{len(sites)} sites, {elapsed_ms:.0f} ms (context {context_id})"
    )

    return AnalysisResponse(
        status="success",
        message=(
            f"Found {len(sites)} candidate pond sites in {area_sqkm:.2f} sq km "
            f"({transform['cell_size_m']:.0f} m grid, {elapsed_ms / 1000:.1f} s)"
        ),
        terrain=terrain,
        candidate_sites=_sites_to_models(sites),
        rivers_detected=bool(rivers.any()),
        data_source="aws-terrain-tiles",
        analysis_time_ms=elapsed_ms,
        context_id=context_id,
    )


@router.post("/analyzeSite")
async def analyze_site(request: CustomSiteRequest):
    """
    Analyze a user-clicked point as a pond site, reusing the cached
    terrain context from the last /analyzeArea call (instant response).
    """
    ctx = _cache.get(request.context_id)
    if ctx is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis context expired or not found. Please analyze the area again.",
        )

    try:
        site = analyze_custom_site(
            ctx["dem"], ctx["fdir"], ctx["acc"], ctx["transform"], ctx["river_mask"],
            lat=request.latitude, lon=request.longitude,
            depth_m=request.pond_depth_m,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Custom site analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    # Remember it so KML export can include it
    ctx["sites"] = [site] + ctx["sites"][: max(0, len(ctx["sites"]) - 1)]

    return {
        "status": "success",
        "context_id": request.context_id,
        "site": _sites_to_models([site])[0],
    }


# ---------------------------------------------------------------------------
# KML export — download results for Google Earth
# ---------------------------------------------------------------------------

@router.get("/export/kml/{context_id}/{site_index}")
async def export_site_kml(context_id: str, site_index: int):
    """Export one candidate site (catchment + pond + marker) as a KML file."""
    ctx = _cache.get(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Analysis context expired or not found.")

    sites = ctx.get("sites", [])
    if site_index < 0 or site_index >= len(sites):
        raise HTTPException(status_code=404, detail=f"Site index {site_index} not found (0..{len(sites)-1}).")

    s = sites[site_index]

    def ring_text(ring: list[dict]) -> str:
        return " ".join(f"{p['longitude']},{p['latitude']},0" for p in ring)

    placemarks: list[str] = []

    loc = s["location"]
    placemarks.append(f"""
    <Placemark><name>Pond Site {site_index + 1}</name>
      <styleUrl>#site</styleUrl>
      <description>Elevation: {s['elevation_m']:.1f} m
Catchment: {s['catchment_area_hectares']:.2f} ha
Pond area: {s['pond_area_sqm']:.0f} sqm
Volume: {s['pond_volume_m3']:.0f} m3</description>
      <Point><coordinates>{loc['longitude']},{loc['latitude']},0</coordinates></Point>
    </Placemark>""")

    if s.get("catchment_boundary"):
        placemarks.append(f"""
    <Placemark><name>Catchment {site_index + 1} ({s['catchment_area_hectares']:.2f} ha)</name>
      <styleUrl>#catchment</styleUrl>
      <Polygon><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode>
        <outerBoundaryIs><LinearRing><coordinates>{ring_text(s['catchment_boundary'])}</coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
    </Placemark>""")

    for j, ring in enumerate(s.get("pond_boundary_rings", [])):
        placemarks.append(f"""
    <Placemark><name>Pond {site_index + 1}{'.' + str(j + 1) if j else ''} ({s['pond_area_sqm']:.0f} sqm, {s['pond_volume_m3']:.0f} m3)</name>
      <styleUrl>#pond</styleUrl>
      <Polygon><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode>
        <outerBoundaryIs><LinearRing><coordinates>{ring_text(ring)}</coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
    </Placemark>""")

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Pond Catchment Analysis — Site {site_index + 1}</name>
  <Style id="site"><IconStyle><color>ff0000ff</color><scale>1.2</scale>
    <Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle></Style>
  <Style id="catchment"><LineStyle><color>ff00a5ff</color><width>3</width></LineStyle>
    <PolyStyle><color>3300a5ff</color></PolyStyle></Style>
  <Style id="pond"><LineStyle><color>ffffff00</color><width>2</width></LineStyle>
    <PolyStyle><color>99ff8800</color></PolyStyle></Style>
  {''.join(placemarks)}
</Document>
</kml>"""

    return Response(
        content=kml,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="pond_site_{site_index + 1}.kml"'},
    )


# ---------------------------------------------------------------------------
# Cache introspection (for monitoring / stress testing)
# ---------------------------------------------------------------------------

@router.get("/cache/stats")
async def cache_stats():
    """Current terrain-context cache statistics."""
    return get_cache_stats()


@router.delete("/cache/{context_id}")
async def cache_delete(context_id: str):
    """Drop a specific cached context (memory management under stress)."""
    if _cache._store.pop(context_id, None) is None:
        raise HTTPException(status_code=404, detail="Context not found.")
    return {"status": "deleted", "context_id": context_id}
