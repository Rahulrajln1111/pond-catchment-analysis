import logging

import numpy as np
from scipy import ndimage

from app.config import settings
from app.models import Coordinate
from app.parsers.kml_parser import ContourLine
from app.analysis.catchment import (
    build_reverse_flow,
    delineate_catchment,
    catchment_area_sqm,
    catchment_boundary,
    mask_to_polygons,
    cell_area_sqm,
    row_to_lat,
    lat_to_row,
    col_to_lon,
    lon_to_col,
)

logger = logging.getLogger(__name__)


def create_river_mask(
    river_contours: list[ContourLine],
    transform: dict,
    buffer_cells: int | None = None,
) -> np.ndarray:
    """
    Create a boolean mask of river exclusion zones.

    River cells (and a buffer around them) are marked True.
    Pond sites should NOT be placed in these zones.

    Args:
        river_contours: List of river contour lines from KML parser.
        transform: DEM transform dict for coordinate conversion.
        buffer_cells: Number of cells to buffer around rivers.

    Returns:
        2D boolean array: True where river exclusion applies.
    """
    if buffer_cells is None:
        buffer_cells = settings.hydrology.stream_buffer_cells

    rows = transform["rows"]
    cols = transform["cols"]
    resolution = transform["resolution"]
    x_min = transform["x_min"]

    mask = np.zeros((rows, cols), dtype=bool)

    # Mark river cells
    for contour in river_contours:
        for lon, lat in contour.coords:
            # Convert coordinates to grid indices (honors row0 convention)
            col = int(lon_to_col(lon, transform))
            row = int(lat_to_row(lat, transform))

            if 0 <= row < rows and 0 <= col < cols:
                mask[row, col] = True

    # Buffer the mask (dilate)
    if buffer_cells > 0:
        struct = np.ones((2 * buffer_cells + 1, 2 * buffer_cells + 1), dtype=bool)
        mask = ndimage.binary_dilation(mask, structure=struct)

    river_cells = int(np.sum(mask))
    logger.info(f"River mask: {river_cells} exclusion cells ({buffer_cells} cell buffer)")
    return mask


def rings_to_coords(rings: list[list[tuple[float, float]]]) -> list[list[dict]]:
    """Convert mask_to_polygons rings to JSON-safe dict rings."""
    return [
        [{"latitude": round(lat, 6), "longitude": round(lon, 6)} for lon, lat in ring]
        for ring in rings
    ]


def simulate_pond(
    dem: np.ndarray,
    catchment: np.ndarray,
    pour_row: int,
    pour_col: int,
    transform: dict,
    depth_m: float | None = None,
) -> dict:
    """
    Simulate filling a pond at the pour point.

    Algorithm:
    1. Water surface elevation (WSE) = pour_point_elevation + depth
    2. Candidate cells = inside catchment AND at/below WSE
    3. The pond is the CONNECTED component of those cells containing the
       pour point (water only fills the basin it is confined to — other
       low cells separated by a ridge are NOT part of this pond)
    4. Volume = Σ (WSE − DEM) × cell area over pond cells
    5. Boundary = traced polygon(s) of the pond mask

    Args:
        dem: Pit-filled DEM array.
        catchment: Boolean catchment array.
        pour_row, pour_col: Grid indices of the pour point.
        transform: DEM transform dict.
        depth_m: Pond depth in meters.

    Returns:
        Dict with pond_boundary (largest polygon), pond_boundary_rings
        (all polygons), area, volume, water surface elevation.
    """
    if depth_m is None:
        depth_m = settings.pond.pond_depth_m

    resolution = transform["resolution"]
    cell_area = transform["cell_size_m"] ** 2

    empty = {
        "pond_boundary": [],
        "pond_boundary_rings": [],
        "pond_area_sqm": 0.0,
        "pond_volume_m3": 0.0,
        "water_surface_elevation_m": 0.0,
        "pond_depth_m": depth_m,
    }

    pour_elev = dem[pour_row, pour_col]
    if not np.isfinite(pour_elev):
        return empty

    water_surface = pour_elev + depth_m

    # Candidate pond cells: inside catchment AND at/below water surface.
    # Use <= so the pour cell itself is always included.
    pond_mask = catchment & (dem <= water_surface) & np.isfinite(dem)

    if not pond_mask.any():
        return empty

    # Keep only the connected component containing the pour point.
    # Structuring element of all 8 neighbors (water spreads diagonally too).
    struct = np.ones((3, 3), dtype=bool)
    labels, n = ndimage.label(pond_mask, structure=struct)
    pour_label = labels[pour_row, pour_col]
    if pour_label == 0:
        # Pour cell was excluded by rounding; fall back to the nearest pond cell
        rr, cc = np.nonzero(pond_mask)
        dists = (rr - pour_row) ** 2 + (cc - pour_col) ** 2
        pour_label = labels[rr[int(np.argmin(dists))], cc[int(np.argmin(dists))]]
    pond_mask = labels == pour_label

    pond_cells = int(np.sum(pond_mask))
    if pond_cells == 0:
        return empty

    # Volume: sum of water depth per submerged cell × cell area
    water_depths = np.where(pond_mask, water_surface - dem, 0.0)
    water_depths = np.maximum(water_depths, 0.0)
    volume_m3 = float(np.sum(water_depths) * cell_area)

    # Area
    area_sqm = float(pond_cells * cell_area)

    # Trace boundary polygons (a pond can have multiple lobes)
    rings = mask_to_polygons(pond_mask, transform)
    pond_boundary_rings = rings_to_coords(rings)

    largest = rings[0] if rings else []
    pond_boundary = [
        {"latitude": round(lat, 6), "longitude": round(lon, 6)}
        for lon, lat in largest
    ]

    logger.info(
        f"Pond: {pond_cells} cells, {area_sqm:.0f} sqm, "
        f"{volume_m3:.0f} m3, depth={depth_m}m"
    )

    return {
        "pond_boundary": pond_boundary,
        "pond_boundary_rings": pond_boundary_rings,
        "pond_area_sqm": area_sqm,
        "pond_volume_m3": volume_m3,
        "water_surface_elevation_m": float(water_surface),
        "pond_depth_m": depth_m,
    }


def _precompute_elevation_percentiles(dem: np.ndarray) -> np.ndarray:
    """
    Percentile rank (0..1) of every cell's elevation, computed once.

    Avoids re-scanning the whole DEM for every candidate cell (O(n²) → O(n log n)).
    """
    finite = np.isfinite(dem)
    flat = dem[finite]
    order = np.argsort(flat, kind="stable")
    ranks = np.empty(flat.shape, dtype=np.float64)
    ranks[order] = np.arange(flat.size, dtype=np.float64) / max(flat.size - 1, 1)

    percentiles = np.full(dem.shape, np.nan, dtype=np.float64)
    percentiles[finite] = ranks
    return percentiles


def _latlon_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast approximate distance between two lat/lon points in meters."""
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * np.cos(np.radians((lat1 + lat2) / 2.0))
    return float(np.hypot(dlat, dlon))


def find_candidate_sites(
    dem: np.ndarray,
    fdir: np.ndarray,
    acc: np.ndarray,
    transform: dict,
    river_mask: np.ndarray,
    reverse_flow: list | None = None,
) -> list[dict]:
    """
    Find and rank candidate pond sites.

    Algorithm:
    1. Find cells with high flow accumulation (natural drainage lines)
    2. Filter out river cells, low-accumulation and high-elevation cells
    3. Enforce minimum spacing so sites are spread across the area
    4. For each candidate, delineate catchment and simulate the pond
    5. Rank by catchment area (larger = more inflow) and return top N

    Args:
        dem: Pit-filled DEM array.
        fdir: D8 flow direction array.
        acc: Flow accumulation array.
        transform: DEM transform dict.
        river_mask: Boolean mask of river exclusion zones.
        reverse_flow: Pre-computed reverse flow map.

    Returns:
        List of candidate site dicts with location, elevation,
        catchment area, pond simulation and boundaries.
    """
    rows, cols = dem.shape
    resolution = transform["resolution"]
    x_min = transform["x_min"]
    cell_size_m = transform["cell_size_m"]

    if reverse_flow is None:
        reverse_flow = build_reverse_flow(fdir)

    # Potential pour points: high accumulation, not on river.
    acc_threshold = max(10.0, float(np.nanmax(acc)) * 0.1)

    percentiles = _precompute_elevation_percentiles(dem)

    # Vectorized candidate pre-filter
    valid = np.isfinite(dem) & ~river_mask & (acc >= acc_threshold) \
        & (percentiles <= settings.pond.elevation_percentile_threshold)

    cand_rows, cand_cols = np.nonzero(valid)
    logger.info(f"Found {len(cand_rows)} raw candidates after filtering")

    if len(cand_rows) == 0:
        return []

    # Local relief check (7x7 window) for the filtered candidates only
    cand_acc = acc[cand_rows, cand_cols]
    order = np.argsort(-cand_acc, kind="stable")  # highest accumulation first

    candidates = []
    for idx in order[: settings.pond.max_candidates * 30]:
        r, c = int(cand_rows[idx]), int(cand_cols[idx])
        r0, r1 = max(0, r - 3), min(rows, r + 4)
        c0, c1 = max(0, c - 3), min(cols, c + 4)
        window = dem[r0:r1, c0:c1]
        relief = float(np.nanmax(window) - np.nanmin(window))
        if relief < settings.pond.min_local_relief:
            continue

        candidates.append({
            "row": r,
            "col": c,
            "lat": row_to_lat(r, transform),
            "lon": col_to_lon(c, transform),
            "elevation": float(dem[r, c]),
            "accumulation": float(acc[r, c]),
            "relief": relief,
            "elev_percentile": float(percentiles[r, c]),
        })

    # Enforce minimum spacing between sites (distinct locations, not the
    # same channel repeated 5 times). Scale with grid resolution.
    min_spacing_m = max(250.0, cell_size_m * 30)

    # Delineate catchments in accumulation order, keeping only sites whose
    # catchment is genuinely distinct (low overlap with already-selected
    # sites) so results are spatially diverse.
    results: list[dict] = []
    selected_masks: list[np.ndarray] = []

    for site in candidates:
        if len(results) >= settings.pond.max_candidates:
            break

        if any(
            _latlon_distance_m(site["lat"], site["lon"], r["_lat"], r["_lon"]) < min_spacing_m
            for r in results
        ):
            continue

        catchment = delineate_catchment(
            fdir, acc, site["row"], site["col"], reverse_flow
        )
        area = catchment_area_sqm(catchment, cell_size_m)

        if area < settings.pond.min_catchment_area_sqm:
            continue

        # Catchment-overlap diversity: skip if >50% IoU with a selected site
        duplicate = False
        for sel_mask in selected_masks:
            inter = int(np.sum(catchment & sel_mask))
            union = int(np.sum(catchment | sel_mask))
            if union > 0 and inter / union > 0.5:
                duplicate = True
                break
        if duplicate:
            continue

        # Check if catchment overlaps river significantly
        river_in_catchment = int(np.sum(catchment & river_mask))
        catchment_total = int(np.sum(catchment))
        river_fraction = river_in_catchment / catchment_total if catchment_total > 0 else 0.0

        catchment_bdy = catchment_boundary(catchment, transform)

        pond = simulate_pond(
            dem, catchment, site["row"], site["col"], transform
        )

        # The pond SURFACE must not overlap the river: a "pond" whose water
        # spreads into the river channel is really a dam on the river.
        # (Catchment overlap alone can be 0% because the catchment is
        # upstream land while the pond floods downstream low ground.)
        water_surface = pond["water_surface_elevation_m"]
        pond_surface = catchment & np.isfinite(dem) & (dem < water_surface)
        pond_cells = int(np.sum(pond_surface))
        if pond_cells == 0:
            continue
        pond_river_fraction = float(np.sum(pond_surface & river_mask)) / pond_cells
        if pond_river_fraction > settings.pond.max_pond_river_fraction:
            logger.info(
                f"  rejecting candidate ({site['lat']:.5f}, {site['lon']:.5f}): "
                f"pond surface overlaps river ({pond_river_fraction:.0%})"
            )
            continue

        results.append({
            "location": {"latitude": site["lat"], "longitude": site["lon"]},
            "_lat": site["lat"],
            "_lon": site["lon"],
            "elevation_m": site["elevation"],
            "catchment_area_sqm": area,
            "catchment_area_hectares": area / 10000,
            "catchment_boundary": catchment_bdy,
            "river_excluded": river_fraction > 0.1 or pond_river_fraction > 0,
            "river_fraction": river_fraction,
            "pond_river_fraction": pond_river_fraction,
            "accumulation": site["accumulation"],
            "pond_boundary": pond["pond_boundary"],
            "pond_boundary_rings": pond["pond_boundary_rings"],
            "pond_area_sqm": pond["pond_area_sqm"],
            "pond_volume_m3": pond["pond_volume_m3"],
            "pond_depth_m": pond["pond_depth_m"],
            "water_surface_elevation_m": pond["water_surface_elevation_m"],
        })
        selected_masks.append(catchment)

    # Sort final results by catchment area (largest first) and strip the
    # internal spacing keys
    results.sort(key=lambda x: x["catchment_area_sqm"], reverse=True)
    for r in results:
        r.pop("_lat", None)
        r.pop("_lon", None)
    results = results[: settings.pond.max_candidates]

    logger.info(f"Selected {len(results)} candidate pond sites")
    for i, r in enumerate(results):
        logger.info(
            f"  Site {i+1}: ({r['location']['latitude']:.6f}, "
            f"{r['location']['longitude']:.6f}) "
            f"elev={r['elevation_m']:.1f}m "
            f"catchment={r['catchment_area_hectares']:.2f}ha "
            f"pond={r['pond_area_sqm']:.0f}sqm vol={r['pond_volume_m3']:.0f}m3"
        )

    return results


def analyze_custom_site(
    dem: np.ndarray,
    fdir: np.ndarray,
    acc: np.ndarray,
    transform: dict,
    river_mask: np.ndarray,
    lat: float,
    lon: float,
    depth_m: float | None = None,
) -> dict:
    """
    Analyze a user-picked pond location (click on the map).

    Snaps the click to the nearest valid cell, delineates its upstream
    catchment, and simulates the pond at the requested depth.

    Returns a site dict in the same shape as find_candidate_sites entries,
    plus row/col used. Raises ValueError if the point is unusable.
    """
    rows, cols = dem.shape
    resolution = transform["resolution"]
    x_min = transform["x_min"]
    cell_size_m = transform["cell_size_m"]

    col = int(round(lon_to_col(lon, transform)))
    row = int(round(lat_to_row(lat, transform)))

    # Snap to nearest cell that has data
    if not (0 <= row < rows and 0 <= col < cols) or not np.isfinite(dem[row, col]):
        finite_rc = np.argwhere(np.isfinite(dem))
        if finite_rc.size == 0:
            raise ValueError("No terrain data at the selected location")
        dists = (finite_rc[:, 0] - row) ** 2 + (finite_rc[:, 1] - col) ** 2
        row, col = int(finite_rc[int(np.argmin(dists))][0]), int(finite_rc[int(np.argmin(dists))][1])

    catchment = delineate_catchment(fdir, acc, row, col)
    area = catchment_area_sqm(catchment, cell_size_m)

    if area < 100.0:
        raise ValueError(
            "Selected point has almost no upstream area — pick a lower point in the terrain"
        )

    river_in_catchment = int(np.sum(catchment & river_mask))
    catchment_total = int(np.sum(catchment))
    river_fraction = river_in_catchment / catchment_total if catchment_total > 0 else 0.0

    catchment_bdy = catchment_boundary(catchment, transform)
    pond = simulate_pond(dem, catchment, row, col, transform, depth_m=depth_m)

    site = {
        "location": {
            "latitude": row_to_lat(row, transform),
            "longitude": col_to_lon(col, transform),
        },
        "elevation_m": float(dem[row, col]),
        "catchment_area_sqm": area,
        "catchment_area_hectares": area / 10000,
        "catchment_boundary": catchment_bdy,
        "river_excluded": river_fraction > 0.1,
        "river_fraction": river_fraction,
        "accumulation": float(acc[row, col]),
        "pond_boundary": pond["pond_boundary"],
        "pond_boundary_rings": pond["pond_boundary_rings"],
        "pond_area_sqm": pond["pond_area_sqm"],
        "pond_volume_m3": pond["pond_volume_m3"],
        "pond_depth_m": pond["pond_depth_m"],
        "water_surface_elevation_m": pond["water_surface_elevation_m"],
    }
    logger.info(
        f"Custom site: ({site['location']['latitude']:.6f}, "
        f"{site['location']['longitude']:.6f}) catchment={area:.0f} sqm"
    )
    return site
