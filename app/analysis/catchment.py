import logging
from collections import deque
 
import numpy as np
 
from app.analysis.hydrology import D8_CODES
 
logger = logging.getLogger(__name__)
 
 
def build_reverse_flow(fdir: np.ndarray) -> list[list[list[tuple[int, int]]]]:

    rows, cols = fdir.shape
    reverse_flow = [[[] for _ in range(cols)] for _ in range(rows)]
 
    for r in range(rows):
        for c in range(cols):
            code = fdir[r, c]
            if code == 0:
                continue
 
            dr, dc = D8_CODES[code]
            nr, nc = r + dr, c + dc
 
            if 0 <= nr < rows and 0 <= nc < cols:
                reverse_flow[nr][nc].append((r, c))
 
    logger.info(f"Reverse flow map built for {rows}x{cols} grid")
    return reverse_flow
 
 
def delineate_catchment(
    fdir: np.ndarray,
    acc: np.ndarray,
    target_row: int,
    target_col: int,
    reverse_flow: list[list[list[tuple[int, int]]]] | None = None,
) -> np.ndarray:
    rows, cols = fdir.shape
 
    if reverse_flow is None:
        reverse_flow = build_reverse_flow(fdir)
 
    # BFS upstream from the target cell
    catchment = np.zeros((rows, cols), dtype=bool)
    queue = deque()
    queue.append((target_row, target_col))
    catchment[target_row, target_col] = True
 
    while queue:
        r, c = queue.popleft()
 
        # Check all cells that flow INTO (r, c)
        for ur, uc in reverse_flow[r][c]:
            if not catchment[ur, uc]:
                catchment[ur, uc] = True
                queue.append((ur, uc))
 
    catchment_count = np.sum(catchment)
    logger.info(
        f"Catchment delineated: {catchment_count} cells "
        f"({catchment_count * _cell_area_sqm(fdir):.0f} sqm)"
    )
    return catchment
 
 
def catchment_area_sqm(
    catchment: np.ndarray,
    cell_size_m: float,
) -> float:
    """
    Calculate catchment area in square meters.
 
    Args:
        catchment: Boolean catchment array.
        cell_size_m: Size of each DEM cell in meters.
 
    Returns:
        Total area in square meters.
    """
    cell_count = np.sum(catchment)
    area = cell_count * cell_size_m * cell_size_m
    return area


def catchment_boundary(
    catchment: np.ndarray,
    transform: dict,
) -> list[dict]:
    """
    Extract ordered boundary polygon from catchment mask.

    Uses boundary tracing + Douglas-Peucker simplification to produce
    a clean, non-jagged polygon suitable for KML visualization.

    Args:
        catchment: 2D boolean array.
        transform: DEM transform dict.

    Returns:
        List of {latitude, longitude} dicts forming a closed polygon.
    """
    from skimage import measure
    from shapely.geometry import Polygon

    rows, cols = catchment.shape
    resolution = transform["resolution"]
    x_min = transform["x_min"]
    y_min = transform["y_min"]

    # Trace boundary using marching squares
    contours = measure.find_contours(catchment.astype(float), 0.5)

    if not contours:
        return []

    # Pick the largest contour
    contour = sorted(contours, key=lambda c: len(c), reverse=True)[0]

    # Convert grid (row, col) to (lon, lat)
    raw_coords = []
    for r, c in contour:
        lon = x_min + c * resolution
        lat = y_min + r * resolution
        raw_coords.append((lon, lat))

    # Close if needed
    if raw_coords[0] != raw_coords[-1]:
        raw_coords.append(raw_coords[0])

    # Build shapely polygon, fix self-intersections, simplify jagged edges
    try:
        poly = Polygon(raw_coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        # Douglas-Peucker simplification: remove zigzag vertices
        poly = poly.simplify(resolution * 2, preserve_topology=True)
        coords = list(poly.exterior.coords)
    except Exception:
        coords = raw_coords

    boundary = [
        {"latitude": round(lat, 6), "longitude": round(lon, 6)}
        for lon, lat in coords
    ]

    return boundary
 
 
 
def _cell_area_sqm(fdir: np.ndarray) -> float:
    """Helper: estimate cell area in sq meters (approximate)."""
    # Rough estimate: 0.0001 degree ≈ 11.1 meters
    return 11.1 * 11.1
