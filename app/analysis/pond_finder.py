import logging
import numpy as np
 
from app.config import settings
from app.parsers.kml_parser import ContourLine
from app.analysis.catchment import (
    build_reverse_flow,
    delineate_catchment,
    catchment_area_sqm,
    catchment_boundary,
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
    y_min = transform["y_min"]
 
    mask = np.zeros((rows, cols), dtype=bool)
 
    # Mark river cells
    for contour in river_contours:
        for lon, lat in contour.coords:
            # Convert coordinates to grid indices
            col = int((lon - x_min) / resolution)
            row = int((lat - y_min) / resolution)
 
            if 0 <= row < rows and 0 <= col < cols:
                mask[row, col] = True
 
    # Buffer the mask (dilate)
    if buffer_cells > 0:
        from scipy.ndimage import binary_dilation
        struct = np.ones((2 * buffer_cells + 1, 2 * buffer_cells + 1), dtype=bool)
        mask = binary_dilation(mask, structure=struct)
 
    river_cells = np.sum(mask)
    logger.info(f"River mask: {river_cells} exclusion cells ({buffer_cells} cell buffer)")
    return mask
 
 
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
    1. Water surface elevation = pour_point_elevation + depth
    2. Find all cells within catchment where DEM < water surface
    3. These cells form the pond (they would be submerged)
    4. Calculate area and volume

    Args:
        dem: Pit-filled DEM array.
        catchment: Boolean catchment array.
        pour_row, pour_col: Grid indices of the pour point.
        transform: DEM transform dict.
        depth_m: Pond depth in meters.

    Returns:
        Dict with pond_boundary, area, volume.
    """
    if depth_m is None:
        depth_m = settings.pond.pond_depth_m

    rows, cols = dem.shape
    resolution = transform["resolution"]
    x_min = transform["x_min"]
    y_min = transform["y_min"]
    cell_area = transform["cell_size_m"] ** 2

    pour_elev = dem[pour_row, pour_col]
    if np.isnan(pour_elev):
        return {"pond_boundary": [], "pond_area_sqm": 0, "pond_volume_m3": 0, "water_surface_elevation_m": 0}

    water_surface = pour_elev + depth_m

    # Find pond cells: inside catchment AND below water surface
    pond_mask = catchment & (dem < water_surface) & (~np.isnan(dem))

    pond_cells = np.sum(pond_mask)
    if pond_cells == 0:
        return {"pond_boundary": [], "pond_area_sqm": 0, "pond_volume_m3": 0, "water_surface_elevation_m": water_surface}

    # Calculate volume: sum of (water_surface - dem) for each pond cell
    water_depths = water_surface - dem
    water_depths[~pond_mask] = 0
    volume_m3 = float(np.sum(water_depths) * cell_area)

    # Area
    area_sqm = float(pond_cells * cell_area)

    # Extract boundary (edge cells of pond_mask)
    padded = np.pad(pond_mask, 1, mode='constant', constant_values=False)
    boundary_mask = np.zeros_like(pond_mask, dtype=bool)
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            if dr == 0 and dc == 0:
                continue
            shifted = padded[1+dr:1+dr+rows, 1+dc:1+dc+cols]
            boundary_mask |= (pond_mask & ~shifted)

    bnd_rows, bnd_cols = np.where(boundary_mask)
    boundary = []
    for r, c in zip(bnd_rows, bnd_cols):
        lon = x_min + c * resolution
        lat = y_min + r * resolution
        boundary.append((lon, lat))

    # Simplify boundary if too many points
    if len(boundary) > 100:
        step = len(boundary) // 100
        boundary = boundary[::step]

    logger.info(
        f"Pond: {pond_cells} cells, {area_sqm:.0f} sqm, "
        f"{volume_m3:.0f} m3, depth={depth_m}m"
    )

    return {
        "pond_boundary": boundary,
        "pond_area_sqm": area_sqm,
        "pond_volume_m3": volume_m3,
        "water_surface_elevation_m": water_surface,
    }


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
    1. Find cells with high accumulation (potential pour points)
    2. Filter out river cells and low-accumulation cells
    3. For each candidate, delineate catchment and calculate area
    4. Rank by catchment area (larger = more water storage)
    5. Return top N candidates
 
    Args:
        dem: Pit-filled DEM array.
        fdir: D8 flow direction array.
        acc: Flow accumulation array.
        transform: DEM transform dict.
        river_mask: Boolean mask of river exclusion zones.
        reverse_flow: Pre-computed reverse flow map.
 
    Returns:
        List of candidate site dicts with location, elevation,
        catchment area, and boundary.
    """
    rows, cols = dem.shape
    resolution = transform["resolution"]
    x_min = transform["x_min"]
    y_min = transform["y_min"]
    cell_size_m = transform["cell_size_m"]
 
    # Build reverse flow if not provided
    if reverse_flow is None:
        reverse_flow = build_reverse_flow(fdir)
 
    # Find potential pour points: high accumulation, not on river
    # Use a threshold: at least 10% of max accumulation
    max_acc = np.nanmax(acc)
    acc_threshold = max(10, max_acc * 0.1)
 
    candidates = []
    for r in range(rows):
        for c in range(cols):
            if np.isnan(dem[r, c]):
                continue
            if river_mask[r, c]:
                continue
            if acc[r, c] < acc_threshold:
                continue
 
            # Calculate elevation percentile
            elev = dem[r, c]
            valid_elevs = dem[~np.isnan(dem)]
            elev_percentile = np.sum(valid_elevs <= elev) / len(valid_elevs)
 
            # Prefer lower elevations (valleys)
            if elev_percentile > settings.pond.elevation_percentile_threshold:
                continue
 
            # Check local relief (is this a real depression?)
            local_min = np.nanmin(dem[max(0,r-3):min(rows,r+4), max(0,c-3):min(cols,c+4)])
            local_max = np.nanmax(dem[max(0,r-3):min(rows,r+4), max(0,c-3):min(cols,c+4)])
            relief = local_max - local_min
 
            if relief < settings.pond.min_local_relief:
                continue
 
            # Convert to coordinates
            lon = x_min + c * resolution
            lat = y_min + r * resolution
 
            candidates.append({
                "row": r,
                "col": c,
                "lon": lon,
                "lat": lat,
                "elevation": elev,
                "accumulation": acc[r, c],
                "relief": relief,
                "elev_percentile": elev_percentile,
            })
 
    logger.info(f"Found {len(candidates)} raw candidates after filtering")
 
    # Sort by accumulation (higher = more water)
    candidates.sort(key=lambda x: x["accumulation"], reverse=True)
 
    # For top candidates, delineate catchment and calculate area
    results = []
    for site in candidates[:settings.pond.max_candidates * 2]:
        # Skip if already have enough good results
        if len(results) >= settings.pond.max_candidates:
            break
 
        # Delineate catchment
        catchment = delineate_catchment(
            fdir, acc, site["row"], site["col"], reverse_flow
        )
        area = catchment_area_sqm(catchment, cell_size_m)
 
        # Filter by minimum catchment area
        if area < settings.pond.min_catchment_area_sqm:
            continue
 
        # Check if catchment overlaps river significantly
        river_in_catchment = np.sum(catchment & river_mask)
        catchment_total = np.sum(catchment)
        river_fraction = river_in_catchment / catchment_total if catchment_total > 0 else 0        # Get boundary
        boundary = catchment_boundary(catchment, transform)

        # Simulate pond
        pond = simulate_pond(
            dem, catchment, site["row"], site["col"], transform
        )

        results.append({
            "location": {"latitude": site["lat"], "longitude": site["lon"]},
            "elevation_m": site["elevation"],
            "catchment_area_sqm": area,
            "catchment_area_hectares": area / 10000,
            "catchment_boundary": [
                {"latitude": lat, "longitude": lon}
                for lon, lat in boundary
            ],
            "river_excluded": river_fraction > 0.1,
            "river_fraction": river_fraction,
            "accumulation": site["accumulation"],
            # Pond fields
            "pond_boundary": [
                {"latitude": lat, "longitude": lon}
                for lon, lat in pond["pond_boundary"]
            ],
            "pond_area_sqm": pond["pond_area_sqm"],
            "pond_volume_m3": pond["pond_volume_m3"],
            "pond_depth_m": settings.pond.pond_depth_m,
            "water_surface_elevation_m": pond["water_surface_elevation_m"],
        })
 
    # Sort final results by catchment area (largest first)
    results.sort(key=lambda x: x["catchment_area_sqm"], reverse=True)
 
    # Trim to max candidates
    results = results[:settings.pond.max_candidates]
 
    logger.info(f"Selected {len(results)} candidate pond sites")
    for i, r in enumerate(results):
        logger.info(
            f"  Site {i+1}: ({r['location']['latitude']:.6f}, "
            f"{r['location']['longitude']:.6f}) "
            f"elev={r['elevation_m']:.1f}m "
            f"catchment={r['catchment_area_hectares']:.2f}ha"
        )
 
    return results
