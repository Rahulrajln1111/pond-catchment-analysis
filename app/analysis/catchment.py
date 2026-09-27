import logging
from collections import deque

import numpy as np

from app.analysis.hydrology import D8_CODES

logger = logging.getLogger(__name__)


def build_reverse_flow(fdir: np.ndarray) -> list[list[list[tuple[int, int]]]]:
    """
    Build a map of which cells flow INTO each cell (for upstream BFS).

    Note: this Python-list version is memory-hungry on large grids. For
    interactive (drawn-area) analysis prefer the vectorized
    `delineate_catchment_vectorized` in this module, which is used
    automatically for grids above a size threshold.
    """
    rows, cols = fdir.shape
    reverse_flow: list[list[list[tuple[int, int]]]] = [
        [[] for _ in range(cols)] for _ in range(rows)
    ]

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


def delineate_catchment_vectorized(
    fdir: np.ndarray,
    target_row: int,
    target_col: int,
) -> np.ndarray:
    """
    Vectorized upstream delineation on the D8 graph.

    Iteratively walks the flow graph "backwards" using array shifts: at
    each step, a cell is added to the catchment if any neighbor already in
    the catchment flows into it. Converges in (longest flow path)
    iterations, each fully vectorized. Equivalent to BFS on the
    reverse-flow graph, but without building it.
    """
    rows, cols = fdir.shape
    catchment = np.zeros((rows, cols), dtype=bool)
    catchment[target_row, target_col] = True

    while True:
        new = np.zeros_like(catchment)
        for code, (a, b) in D8_CODES.items():
            # A cell N = (i-a, j-b) flows INTO T = (i, j) exactly when
            # fdir[N] == code (its offset (a, b) lands on T).
            # Valid targets: N must stay in-grid.
            tr0 = max(0, a)
            tr1 = rows + min(0, a)
            tc0 = max(0, b)
            tc1 = cols + min(0, b)
            if tr0 >= tr1 or tc0 >= tc1:
                continue
            r0, r1 = tr0 - a, tr1 - a
            c0, c1 = tc0 - b, tc1 - b

            # Grow upstream: N joins if T is in the catchment and N flows in.
            new[r0:r1, c0:c1] |= catchment[tr0:tr1, tc0:tc1] & (
                fdir[r0:r1, c0:c1] == code
            )

        new &= ~catchment
        if not new.any():
            break
        catchment |= new

    return catchment


def delineate_catchment(
    fdir: np.ndarray,
    acc: np.ndarray,
    target_row: int,
    target_col: int,
    reverse_flow: list | None = None,
) -> np.ndarray:
    """
    Delineate the catchment (all upstream cells) draining to a pour point.

    Dispatches to the vectorized version for large grids (fast, used by the
    interactive map flow) and keeps the BFS version for small grids where
    building the reverse-flow map is cheap and it can be reused across
    many pour points.
    """
    rows, cols = fdir.shape
    if rows * cols > 250_000:
        return delineate_catchment_vectorized(fdir, target_row, target_col)

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
        f"({catchment_count * _cell_area_sqm():.0f} sqm approx)"
    )
    return catchment


def row_to_lat(r: float, transform: dict) -> float:
    """Grid row -> latitude, honoring the transform's row0 convention.

    row0='north' (terrain tiles): row 0 is the northern edge.
    row0='south' (contour KML grids): row 0 is the southern edge.
    """
    res_y = transform.get("res_y", transform.get("resolution", 0.0))
    if transform.get("row0", "south") == "north":
        return transform["y_max"] - r * res_y
    return transform["y_min"] + r * res_y


def lat_to_row(lat: float, transform: dict) -> float:
    """Latitude -> grid row (inverse of row_to_lat)."""
    res_y = transform.get("res_y", transform.get("resolution", 0.0))
    if transform.get("row0", "south") == "north":
        return (transform["y_max"] - lat) / res_y
    return (lat - transform["y_min"]) / res_y


def col_to_lon(c: float, transform: dict) -> float:
    """Grid column -> longitude (columns always ascend eastwards)."""
    res_x = transform.get("res_x", transform.get("resolution", 0.0))
    return transform["x_min"] + c * res_x


def lon_to_col(lon: float, transform: dict) -> float:
    """Longitude -> grid column (inverse of col_to_lon)."""
    res_x = transform.get("res_x", transform.get("resolution", 0.0))
    return (lon - transform["x_min"]) / res_x


def cell_area_sqm(transform: dict) -> float:
    """
    Accurate cell area in square meters from the transform.

    Handles non-square degree cells: latitude/longitude have different
    metric sizes. Uses local scale factors at the grid center latitude.
    """
    lat_center = (transform["y_min"] + transform["y_max"]) / 2.0
    res_lat = transform.get("res_y", transform.get("resolution", 0.0))
    res_lon = transform.get("res_x", transform.get("resolution", 0.0))

    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * np.cos(np.radians(lat_center))

    return float(res_lat * meters_per_deg_lat * res_lon * meters_per_deg_lon)


def catchment_area_sqm(
    catchment: np.ndarray,
    cell_size_m: float,
) -> float:
    """
    Catchment area in square meters.

    Args:
        catchment: Boolean catchment array.
        cell_size_m: Size of each DEM cell in meters.

    Returns:
        Total area in square meters.
    """
    cell_count = np.sum(catchment)
    area = cell_count * cell_size_m * cell_size_m
    return float(area)


def mask_to_polygons(
    mask: np.ndarray,
    transform: dict,
    simplify_factor: float = 2.0,
) -> list[list[tuple[float, float]]]:
    """
    Trace ALL polygons in a boolean mask (marching squares), simplify with
    Douglas-Peucker, and convert to (lon, lat) rings.

    Returns a list of closed rings [(lon, lat), ...], largest first.
    """
    from app.analysis.polygons import find_contours_mask, simplify_ring

    res_x = transform.get("res_x", transform.get("resolution"))
    x_min = transform["x_min"]

    rings: list[list[tuple[float, float]]] = []
    for ring_rc in find_contours_mask(mask):
        ring_ll = [
            (x_min + c * res_x, row_to_lat(r, transform))
            for r, c in ring_rc
        ]
        ring_ll = simplify_ring(ring_ll, res_x * simplify_factor)
        if len(ring_ll) >= 4:
            rings.append(ring_ll)

    return rings


def catchment_boundary(
    catchment: np.ndarray,
    transform: dict,
) -> list[dict]:
    """
    Extract ordered boundary polygon from catchment mask.

    Uses boundary tracing + Douglas-Peucker simplification to produce
    a clean, non-jagged polygon suitable for map visualization.

    Returns:
        List of {latitude, longitude} dicts forming a closed polygon.
    """
    rings = mask_to_polygons(catchment, transform)
    if not rings:
        return []

    return [
        {"latitude": round(lat, 6), "longitude": round(lon, 6)}
        for lon, lat in rings[0]
    ]


def _cell_area_sqm(fdir: np.ndarray | None = None) -> float:
    """Legacy helper: rough cell area (kept for backward compatibility)."""
    return 11.1 * 11.1
