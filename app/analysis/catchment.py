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
) -> list[tuple[float, float]]:
    """
    Extract the boundary polygon of a catchment area.
 
    Uses a simple edge-detection approach: a cell is on the boundary
    if it's inside the catchment AND has at least one neighbor outside.
 
    Args:
        catchment: Boolean catchment array.
        transform: DEM transform dict with geo-referencing info.
 
    Returns:
        List of (longitude, latitude) tuples forming the boundary.
    """
    rows, cols = catchment.shape
 
    # Pad with False to handle edge cells
    padded = np.pad(catchment, 1, mode='constant', constant_values=False)
 
    # A cell is on the boundary if it's True and has any False neighbor
    boundary_mask = np.zeros_like(catchment, dtype=bool)
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            if dr == 0 and dc == 0:
                continue
            # Shift the padded array and compare
            shifted = padded[1+dr:1+dr+rows, 1+dc:1+dc+cols]
            boundary_mask |= (catchment & ~shifted)
 
    # Convert boundary cells to coordinates
    boundary_rows, boundary_cols = np.where(boundary_mask)
    coords = []
    for r, c in zip(boundary_rows, boundary_cols):
        lon = transform["x_min"] + c * transform["resolution"]
        lat = transform["y_min"] + r * transform["resolution"]
        coords.append((lon, lat))
 
    # Simplify: keep only every Nth point to reduce polygon size
    # (optional, for cleaner output)
    if len(coords) > 200:
        step = len(coords) // 200
        coords = coords[::step]
 
    logger.info(f"Boundary extracted: {len(coords)} vertices")
    return coords
 
 
def _cell_area_sqm(fdir: np.ndarray) -> float:
    """Helper: estimate cell area in sq meters (approximate)."""
    # Rough estimate: 0.0001 degree ≈ 11.1 meters
    return 11.1 * 11.1
