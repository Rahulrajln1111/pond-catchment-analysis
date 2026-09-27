"""
DEM extraction from AWS Terrain Tiles (Terrarium encoding).

The global terrain dataset is hosted on AWS Open Data (S3 bucket
`elevation-tiles-prod`) and requires NO API key. Tiles are PNG images
where elevation is encoded as:

    elevation_m = (R * 256 + G + B / 256) - 32768

Sources: https://registry.opendata.aws/terrain-tiles/
Data derives from SRTM, GMTED2010, ETOPO1 and other open DEMs.

This module downloads the tiles covering a drawn polygon, mosaics them
into a single elevation grid, and returns a DEM + transform dict that is
compatible with the existing analysis pipeline (dem_builder.transform).
"""

import logging
import math
import os
import time
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
CACHE_DIR = os.environ.get("TERRAIN_CACHE_DIR", "/tmp/terrain_cache")
HTTP_TIMEOUT = 30  # seconds per tile

# Limits to keep memory / latency bounded (stress + scaling consideration)
MAX_TILES = 120          # max tiles downloaded for one selection
MAX_GRID_CELLS = 2_500_000  # ~2500x1000 grid; ~20 MB float64 per array

_USER_AGENT = "pond-catchment-analysis/2.0 (academic project)"


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Web-Mercator slippy-map tile indices for a lon/lat at a zoom level."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    x = min(max(x, 0), n - 1)
    y = min(max(y, 0), n - 1)
    return x, y


def tile_bounds(tx: int, ty: int, zoom: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees for a slippy tile."""
    n = 2 ** zoom
    west = tx / n * 360.0 - 180.0
    east = (tx + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + 1) / n))))
    return west, south, east, north


def pixel_size_m(zoom: int, lat: float) -> float:
    """Approximate ground size of one tile pixel (meters) at a latitude."""
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


def choose_zoom(bbox: tuple[float, float, float, float], target_cell_m: float) -> int:
    """Pick a zoom level whose pixel size is closest to the target cell size."""
    lat_center = (bbox[1] + bbox[3]) / 2.0
    ideal = 156543.03392 * math.cos(math.radians(lat_center)) / target_cell_m
    zoom = int(round(math.log2(ideal)))
    return min(max(zoom, 8), 15)


def fetch_tile(z: int, x: int, y: int) -> np.ndarray:
    """Download one terrarium tile (with disk cache); return RAW RGB pixels.

    Decoding to meters happens once for the whole mosaic (see decode_mosaic).
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{z}_{x}_{y}.png")

    if not os.path.exists(cache_path):
        url = TILE_URL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        last_err: Exception | None = None
        for attempt in range(3):  # simple retry for transient network errors
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    data = resp.read()
                with open(cache_path, "wb") as f:
                    f.write(data)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001 - retry any network error
                last_err = e
                time.sleep(0.5 * (attempt + 1))
        if last_err is not None:
            raise RuntimeError(f"Failed to download terrain tile {z}/{x}/{y}: {last_err}")

    img = Image.open(cache_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img, dtype=np.uint8)


def decode_mosaic(mosaic: np.ndarray) -> np.ndarray:
    """Terrarium PNG mosaic (H, W, 3) uint8 -> elevation meters (H, W)."""
    r = mosaic[:, :, 0].astype(np.float64)
    g = mosaic[:, :, 1].astype(np.float64)
    b = mosaic[:, :, 2].astype(np.float64)
    return (r * 256.0 + g + b / 256.0) - 32768.0


def polygon_bbox(geom: dict) -> tuple[float, float, float, float]:
    """
    Bounding box (west, south, east, north) of a GeoJSON Polygon or MultiPolygon.

    Coordinates must be in GeoJSON order [longitude, latitude].
    """
    if geom.get("type") == "MultiPolygon":
        rings = [ring for poly in geom["coordinates"] for ring in poly]
    elif geom.get("type") == "Polygon":
        rings = geom["coordinates"]
    else:
        raise ValueError(f"Unsupported geometry type: {geom.get('type')}")

    lons: list[float] = []
    lats: list[float] = []
    for ring in rings:
        for coord in ring:
            lon, lat = float(coord[0]), float(coord[1])
            if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
                raise ValueError(
                    f"Coordinate ({coord[0]}, {coord[1]}) is out of range. "
                    "Expected [longitude, latitude] (GeoJSON standard)."
                )
            lons.append(lon)
            lats.append(lat)

    return min(lons), min(lats), max(lons), max(lats)


def build_dem_from_polygon(
    geom: dict,
    target_cell_m: float = 10.0,
) -> dict:
    """
    Build a DEM grid for a GeoJSON Polygon/MultiPolygon selection.

    Returns a dict compatible with the existing pipeline:
        {
            "dem": 2D np.ndarray (meters, NaN outside the polygon),
            "transform": {x_min, x_max, y_min, y_max, res_x, res_y,
                          rows, cols, cell_size_m, resolution, zoom},
            "bbox": (west, south, east, north),
            "zoom": int,
            "tiles_fetched": int,
        }
    """
    west, south, east, north = polygon_bbox(geom)
    if east - west < 1e-6 or north - south < 1e-6:
        raise ValueError("Selected area is too small. Draw a larger region.")

    lat_center = (south + north) / 2.0
    zoom = choose_zoom((west, south, east, north), target_cell_m)

    # Tile range covering the bbox
    tx0, ty0 = lonlat_to_tile(west, north, zoom)  # north -> smaller y
    tx1, ty1 = lonlat_to_tile(east, south, zoom)
    nx_tiles = abs(tx1 - tx0) + 1
    ny_tiles = abs(ty1 - ty0) + 1

    # Lower zoom if the selection would need too many tiles
    while nx_tiles * ny_tiles > MAX_TILES and zoom > 8:
        zoom -= 1
        tx0, ty0 = lonlat_to_tile(west, north, zoom)
        tx1, ty1 = lonlat_to_tile(east, south, zoom)
        nx_tiles = abs(tx1 - tx0) + 1
        ny_tiles = abs(ty1 - ty0) + 1

    if nx_tiles * ny_tiles > MAX_TILES:
        raise ValueError("Selected area is too large. Please draw a smaller region.")

    # Download tiles into a mosaic
    x_start, x_end = min(tx0, tx1), max(tx0, tx1)
    y_start, y_end = min(ty0, ty1), max(ty0, ty1)
    mosaic_w = (x_end - x_start + 1) * 256
    mosaic_h = (y_end - y_start + 1) * 256
    mosaic = np.zeros((mosaic_h, mosaic_w, 3), dtype=np.uint8)
    fetched = 0
    for ty in range(y_start, y_end + 1):
        for tx in range(x_start, x_end + 1):
            tile = fetch_tile(zoom, tx, ty)
            r = (ty - y_start) * 256
            c = (tx - x_start) * 256
            mosaic[r:r + 256, c:c + 256, :] = tile[:, :, :3]
            fetched += 1

    elev_mosaic = decode_mosaic(mosaic)

    # NODATA (oceans / voids) -> NaN
    elev_mosaic[elev_mosaic <= -32000] = np.nan

    # Crop the mosaic to the exact bbox in pixel space
    n = 2 ** zoom
    world_px = n * 256
    px_west = (west + 180.0) / 360.0 * world_px - x_start * 256
    px_east = (east + 180.0) / 360.0 * world_px - x_start * 256

    def _lat_to_px_y(lat: float) -> float:
        lat_rad = math.radians(lat)
        merc = math.asinh(math.tan(lat_rad))  # in (-pi, pi)
        return (1.0 - merc / math.pi) / 2.0 * world_px - y_start * 256

    px_north = _lat_to_px_y(north)
    px_south = _lat_to_px_y(south)

    c0 = int(math.floor(px_west))
    c1 = int(math.ceil(px_east))
    r0 = int(math.floor(px_north))
    r1 = int(math.ceil(px_south))
    c0 = max(c0, 0)
    r0 = max(r0, 0)
    c1 = min(c1, mosaic_w)
    r1 = min(r1, mosaic_h)

    dem = elev_mosaic[r0:r1, c0:c1].copy()

    if dem.shape[0] < 8 or dem.shape[1] < 8:
        raise ValueError("Selected area is too small for analysis. Draw a larger region.")

    if dem.shape[0] * dem.shape[1] > MAX_GRID_CELLS:
        raise ValueError("Selected area is too large for analysis. Draw a smaller region.")

    # Transform convention (shared with catchment/pond_finder helpers):
    #   cell (row, col) CENTER = (x_min + col*res_x,  y_max - row*res_y)
    # i.e. x_min/y_max are the centers of the first column / top row.
    # Longitude is linear in Web Mercator; latitude is linearized over the
    # small bbox (sub-centimeter error at these scales).
    world_px = n * 256
    res_x = 360.0 / world_px
    x_min = (x_start * 256 + c0 + 0.5) / world_px * 360.0 - 180.0

    def _px_y_to_lat(v: float) -> float:
        merc = (1.0 - 2.0 * v / world_px) * math.pi
        return math.degrees(math.atan(math.sinh(merc)))

    y_max = _px_y_to_lat(y_start * 256 + r0 + 0.5)   # center of top row
    y_min = _px_y_to_lat(y_start * 256 + r1 - 0.5)   # center of bottom row
    res_y = (y_max - y_min) / max(dem.shape[0] - 1, 1)
    cell_size_m = pixel_size_m(zoom, lat_center)

    # Mask out everything outside the drawn polygon (rasterize in grid space)
    pad = Image.new("L", (dem.shape[1], dem.shape[0]), 0)
    draw = ImageDraw.Draw(pad)

    def _add_ring(ring: list) -> None:
        pts = [
            ((float(p[0]) - x_min) / res_x, (y_max - float(p[1])) / res_y)
            for p in ring
        ]
        if len(pts) >= 3:
            draw.polygon(pts, outline=1, fill=1)

    if geom.get("type") == "MultiPolygon":
        for poly in geom["coordinates"]:
            _add_ring(poly[0])
    else:
        _add_ring(geom["coordinates"][0])

    outside = np.asarray(pad) == 0
    dem[outside] = np.nan
    # Keep a small margin so the polygon edge has valid neighbors for flow
    from scipy.ndimage import binary_dilation
    grow = binary_dilation(~outside, iterations=2)
    dem[grow] = np.nan_to_num(dem[grow], nan=float(np.nanmean(dem)))

    transform = {
        "x_min": float(x_min),
        "x_max": float(x_min + (dem.shape[1] - 1) * res_x),
        "y_min": float(y_min),
        "y_max": float(y_max),
        "res_x": float(res_x),
        "res_y": float(res_y),
        "resolution": float((res_x + res_y) / 2.0),  # backward-compatible field
        "rows": int(dem.shape[0]),
        "cols": int(dem.shape[1]),
        "cell_size_m": float(cell_size_m),
        "row0": "north",
        "zoom": zoom,
        "source": "aws-terrarium",
    }

    logger.info(
        f"Terrain DEM ready: {dem.shape[1]}x{dem.shape[0]} cells, zoom {zoom}, "
        f"{fetched} tiles, cell ≈ {cell_size_m:.1f} m, "
        f"elev {np.nanmin(dem):.1f}–{np.nanmax(dem):.1f} m"
    )

    return {
        "dem": dem,
        "transform": transform,
        "bbox": (west, south, east, north),
        "zoom": zoom,
        "tiles_fetched": fetched,
    }
