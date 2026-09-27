"""
River geometry from OpenStreetMap (Overpass API).

DEM-based river extraction (flow-accumulation thresholding) is unreliable:
the threshold is terrain-dependent, and pour points always sit on drainage
lines, so sites are either over-excluded or the real river is missed.

The reliable source for "where is the river" is OpenStreetMap: rivers,
streams and canals are surveyed vector geometry. This module queries the
free Overpass API (no key required) for waterways around the selected
polygon, rasterizes them onto the DEM grid, and dilates the lines by a
width buffer so ponds are never suggested on or next to a river.

References:
    - Overpass API: https://wiki.openstreetmap.org/wiki/Overpass_API
    - Overpass QL:  https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL
    - Waterway tags: https://wiki.openstreetmap.org/wiki/Waterways
"""

import logging
import math
import time
import urllib.request
import urllib.parse
import json

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# Public Overpass endpoints (tried in order). No API key needed.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

HTTP_TIMEOUT = 8           # seconds per request (Overpass can be slow/flaky;
                           # fail fast so the DEM fallback kicks in quickly)
WATERWAY_BUFFER_M = 40.0   # half-width of the river exclusion zone (meters)

# waterway values that behave like a river/ large channel
RIVER_WATERWAYS = {"river", "stream", "canal", "tidal_channel", "riverbank"}

_USER_AGENT = "pond-catchment-analysis/2.0 (academic project)"


def _bbox(geom: dict) -> tuple[float, float, float, float]:
    """(south, west, north, east) bbox of a GeoJSON Polygon/MultiPolygon."""
    lons, lats = [], []
    rings = (
        [ring for poly in geom["coordinates"] for ring in poly]
        if geom.get("type") == "MultiPolygon"
        else geom["coordinates"]
    )
    for ring in rings:
        for coord in ring:
            lons.append(float(coord[0]))
            lats.append(float(coord[1]))
    return min(lats), min(lons), max(lats), max(lons)


def fetch_osm_waterways(geom: dict) -> list[dict]:
    """
    Query Overpass for mapped waterways intersecting the polygon bbox.

    Returns a list of {"way_id": int, "coords": [(lon, lat), ...]} polylines.
    Raises RuntimeError when all endpoints fail (caller may fall back to
    DEM-based detection).
    """
    south, west, north, east = _bbox(geom)

    # Overpass QL: waterway polylines in the bbox (bbox order: S,W,N,E)
    query = (
        f"[out:json][timeout:25];"
        f'way["waterway"~"^({ "|".join(sorted(RIVER_WATERWAYS)) })$"]'
        f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});"
        f"out geom;"
    )

    last_err: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(
                endpoint,
                data=data,
                headers={"User-Agent": _USER_AGENT},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode())

            ways: list[dict] = []
            for el in payload.get("elements", []):
                if el.get("type") != "way" or "geometry" not in el:
                    continue
                coords = [
                    (pt["lon"], pt["lat"]) for pt in el["geometry"] if pt is not None
                ]
                if len(coords) >= 2:
                    ways.append({"way_id": el.get("id"), "coords": coords})

            logger.info(f"OSM waterways: {len(ways)} lines from {endpoint}")
            return ways
        except Exception as e:  # noqa: BLE001 - try the next endpoint
            last_err = e
            logger.warning(f"Overpass endpoint failed ({endpoint}): {e}")
            time.sleep(1)

    raise RuntimeError(f"All Overpass endpoints failed: {last_err}")


def _meters_per_deg_lat() -> float:
    return 111_320.0


def _meters_per_deg_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def rasterize_river_mask(
    waterways: list[dict],
    transform: dict,
    rows: int,
    cols: int,
    buffer_m: float = WATERWAY_BUFFER_M,
) -> np.ndarray:
    """
    Rasterize OSM waterway polylines onto the DEM grid.

    Lines are drawn onto an image the size of the grid and dilated by
    buffer_m meters (converted to grid cells) so the exclusion zone covers
    the river's actual width.
    """
    mask = Image.new("L", (cols, rows), 0)
    draw = ImageDraw.Draw(mask)

    lat_center = (transform["y_min"] + transform["y_max"]) / 2.0
    res_x = transform["res_x"]
    res_y = transform["res_y"]
    x_min = transform["x_min"]
    y_max = transform["y_max"]

    drawn = 0
    for way in waterways:
        pts = []
        for lon, lat in way["coords"]:
            # Grid convention: cell (row, col) center = (x_min + col*res_x,
            # y_max - row*res_y)  (same as terrain_tiles / dem_builder).
            px = (lon - x_min) / res_x
            py = (y_max - lat) / res_y
            pts.append((px, py))
        if len(pts) >= 2:
            draw.line(pts, fill=255, width=1)
            drawn += 1

    logger.info(f"Rasterized {drawn} OSM waterway lines onto {rows}x{cols} grid")

    arr = np.asarray(mask) > 0

    # Dilate by buffer meters -> cells (use an average resolution; lat/lon
    # differ slightly but the buffer is an exclusion margin, not exact science)
    cell_m = transform.get("cell_size_m", 10.0)
    buf_cells = max(1, int(round(buffer_m / cell_m)))
    if arr.any() and buf_cells > 1:
        from scipy.ndimage import binary_dilation

        arr = binary_dilation(arr, iterations=buf_cells)

    logger.info(
        f"River mask from OSM: {int(arr.sum())} cells "
        f"({100.0 * arr.sum() / arr.size:.2f}% of grid, {buf_cells}-cell buffer)"
    )
    return arr


def river_mask_from_osm(
    geom: dict,
    transform: dict,
    dem: np.ndarray,
) -> tuple[np.ndarray, list[dict]]:
    """
    Build the river mask for a drawn polygon from OpenStreetMap data.

    Returns (mask, waterways). Raises RuntimeError if Overpass is
    unreachable (caller should fall back to DEM-based detection).
    """
    rows, cols = dem.shape
    ways = fetch_osm_waterways(geom)
    mask = rasterize_river_mask(ways, transform, rows, cols)
    return mask, ways
