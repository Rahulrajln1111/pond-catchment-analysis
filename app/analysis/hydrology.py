"""
Hydrological analysis: sink filling, D8 flow direction, flow accumulation.

Phase II implementations use only numpy + stdlib (no scikit-image):

- fill_sinks: priority-flood depression filling (Barnes et al. 2014) with a
  binary heap. Nodata cells (outside the drawn selection) act as spillways.
- compute_flow_direction: vectorized D8 steepest descent, then flat
  resolution by BFS so every cell (including filled depressions and natural
  flats) has a defined downslope path to the domain edge.
- compute_flow_accumulation: level-synchronous topological accumulation
  (Kahn's algorithm by waves) — exact on flats, O(edges) total.
"""

import logging
from collections import deque
from heapq import heappush, heappop

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

# D8 neighbor offsets (dr, dc) in ESRI power-of-2 encoding
D8_OFFSETS = [
    (0, 1, 2),      # East
    (1, 1, 4),      # Southeast
    (1, 0, 8),      # South
    (1, -1, 16),    # Southwest
    (0, -1, 32),    # West
    (-1, -1, 64),   # Northwest
    (-1, 0, 128),   # North
    (-1, 1, 1),     # Northeast
]

# Backward-compatible mapping {code: (dr, dc)} used by catchment.py
D8_CODES = {code: (dr, dc) for dr, dc, code in D8_OFFSETS}

DIAG_FACTOR = 1.41421356237  # sqrt(2)


def _spill_cells(finite: np.ndarray) -> np.ndarray:
    """Valid cells on the array border or adjacent to nodata (spill points)."""
    nan_pad = np.pad(~finite, 1, mode="constant", constant_values=True)
    touches = (
        nan_pad[:-2, 1:-1] | nan_pad[2:, 1:-1]    # N / S
        | nan_pad[1:-1, :-2] | nan_pad[1:-1, 2:]  # W / E
        | nan_pad[:-2, :-2] | nan_pad[:-2, 2:]    # NW / NE
        | nan_pad[2:, :-2] | nan_pad[2:, 2:]      # SW / SE
    )
    rows, cols = finite.shape
    border = np.zeros_like(finite)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    return finite & (touches | border)


def fill_sinks(dem: np.ndarray) -> np.ndarray:
    """
    Fill sinks so every cell drains to the domain edge (priority-flood).

    Pure-Python heap implementation (Barnes et al. 2014): start from spill
    cells (domain border / next to nodata), always process the lowest cell
    next; any lower unvisited neighbor is a sink and gets raised to the
    current level. Nodata cells are skipped (they act as spillways).

    No epsilon gradient is applied; flat routing is resolved later in
    compute_flow_direction via BFS across flats.
    """
    rows, cols = dem.shape
    finite = np.isfinite(dem)
    if not finite.any():
        raise ValueError("DEM contains no valid elevation data")

    filled = np.where(finite, dem, np.inf).astype(np.float64)
    visited = np.zeros((rows, cols), dtype=bool)
    heap: list[tuple[float, int, int]] = []

    for r, c in zip(*np.nonzero(_spill_cells(finite))):
        heappush(heap, (float(filled[r, c]), int(r), int(c)))
        visited[r, c] = True

    n_raised = 0
    while heap:
        elev, r, c = heappop(heap)
        for dr, dc, _code in D8_OFFSETS:
            nr, nc = r + dr, c + dc
            if nr < 0 or nr >= rows or nc < 0 or nc >= cols or visited[nr, nc]:
                continue
            visited[nr, nc] = True
            if not finite[nr, nc]:
                continue  # nodata: never flood through it
            if filled[nr, nc] < elev:
                filled[nr, nc] = elev
                n_raised += 1
            heappush(heap, (float(filled[nr, nc]), nr, nc))

    result = np.where(finite, filled, np.nan)
    logger.info(
        f"Sink filling complete (priority-flood): {int(np.sum(finite))} cells, "
        f"{n_raised} sink cells raised"
    )
    return result


def compute_flow_direction(filled: np.ndarray) -> np.ndarray:
    """
    D8 flow direction on the filled DEM.

    1. Vectorized steepest-descent over all 8 neighbors.
    2. Flat resolution: cells with no strictly-lower neighbor (filled
       depressions, natural flats) are routed by BFS outward from cells
       that DO have a downslope neighbor, across equal-elevation neighbors.
       Every valid cell ends with a path to the domain edge.
    """
    rows, cols = filled.shape
    valid = np.isfinite(filled)

    pad = np.full((rows + 2, cols + 2), np.nan, dtype=np.float64)
    pad[1:-1, 1:-1] = filled

    steepest = np.full((rows, cols), -np.inf, dtype=np.float64)
    fdir = np.zeros((rows, cols), dtype=np.int32)

    for dr, dc, code in D8_OFFSETS:
        neigh = pad[1 + dr: 1 + dr + rows, 1 + dc: 1 + dc + cols]
        dist = DIAG_FACTOR if (dr != 0 and dc != 0) else 1.0
        slope = (filled - neigh) / dist
        better = valid & np.isfinite(slope) & (slope > steepest)
        steepest[better] = slope[better]
        fdir[better] = code

    # --- Flat resolution: BFS from cells that already flow downhill ---
    flat = valid & (fdir == 0)
    if flat.any():
        queue: deque[tuple[int, int]] = deque()

        # Seed: non-flat valid cells that touch a same-elevation flat cell
        seed_mask = np.zeros_like(flat)
        fpad = np.pad(flat, 1, constant_values=False)
        epad = np.pad(filled, 1, constant_values=np.nan)
        for dr, dc, _code in D8_OFFSETS:
            neigh_flat = fpad[1 + dr: 1 + dr + rows, 1 + dc: 1 + dc + cols]
            neigh_elev = epad[1 + dr: 1 + dr + rows, 1 + dc: 1 + dc + cols]
            same_elev = np.isfinite(neigh_elev) & (neigh_elev == filled)
            seed_mask |= (~flat) & (fdir > 0) & neigh_flat & same_elev

        for r, c in zip(*np.nonzero(seed_mask)):
            queue.append((int(r), int(c)))

        resolved = 0
        while queue:
            r, c = queue.popleft()
            for dr, dc, code in D8_OFFSETS:
                nr, nc = r + dr, c + dc
                if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                    continue
                if not flat[nr, nc]:
                    continue
                if filled[nr, nc] != filled[r, c]:
                    continue
                # Flat cell (nr,nc) flows toward (r,c): reverse of (dr,dc)
                back = next(
                    cd for a, b, cd in D8_OFFSETS if (a, b) == (-dr, -dc)
                )
                fdir[nr, nc] = back
                flat[nr, nc] = False
                resolved += 1
                queue.append((nr, nc))
        if resolved:
            logger.info(f"Flat resolution: {resolved} flat cells routed by BFS")

    flow_cells = int(np.sum(fdir > 0))
    logger.info(f"Flow direction computed: {flow_cells} cells with outgoing flow")
    return fdir


def compute_flow_accumulation(fdir: np.ndarray, dem: np.ndarray) -> np.ndarray:
    """
    Flow accumulation: number of upstream cells draining through each cell.

    Level-synchronous topological accumulation (Kahn's algorithm by waves):
    each cell has exactly one downstream neighbor; a cell forwards its
    accumulated total once ALL cells flowing into it have forwarded theirs.
    Exact on flats (no elevation-order assumption), O(number of edges).
    """
    rows, cols = fdir.shape
    n_cells = rows * cols
    valid = np.isfinite(dem)
    valid_flat = valid.ravel()

    lin = np.arange(n_cells, dtype=np.int64).reshape(rows, cols)

    # Downstream linear index per cell (-1 = outlet / nodata)
    dst = np.full(n_cells, -1, dtype=np.int64)
    for code, (dr, dc) in D8_CODES.items():
        tr0, tr1 = max(0, -dr), rows - max(0, dr)
        tc0, tc1 = max(0, -dc), cols - max(0, dc)
        if tr0 >= tr1 or tc0 >= tc1:
            continue
        sub = fdir[tr0:tr1, tc0:tc1]
        tgt = lin[tr0:tr1, tc0:tc1][sub == code]
        if tgt.size:
            dst[tgt] = lin[tr0 + dr: tr1 + dr, tc0 + dc: tc1 + dc][sub == code]

    # Pending upstream-contribution counts
    up = np.zeros(n_cells, dtype=np.int64)
    fwd = dst[valid_flat]
    fwd = fwd[fwd >= 0]
    if fwd.size:
        up += np.bincount(fwd, minlength=n_cells).astype(np.int64)

    acc = np.ones(n_cells, dtype=np.float64)
    processed = ~valid_flat
    ready = valid_flat & (up == 0)

    waves = 0
    while True:
        srcs = np.nonzero(ready)[0]
        if srcs.size == 0:
            break
        waves += 1
        ready[srcs] = False
        processed[srcs] = True

        d = dst[srcs]
        send = d >= 0
        if not np.any(send):
            continue
        targets = d[send]
        np.add.at(acc, targets, acc[srcs[send]])
        np.subtract.at(up, targets, 1)
        newly = targets[up[targets] == 0]
        ready[newly] = True
        if waves > n_cells:  # pragma: no cover — acyclic graph cannot reach this
            logger.warning("Flow accumulation wave limit hit; stopping early")
            break

    acc_grid = np.where(valid, acc.reshape(rows, cols), 0.0)
    logger.info(
        f"Flow accumulation computed (waves={waves}): "
        f"max={float(acc_grid.max()):.0f}, mean={float(acc_grid.mean()):.1f}"
    )
    return acc_grid


def detect_streams(
    fdir: np.ndarray,
    acc: np.ndarray,
    threshold: int | None = None,
) -> np.ndarray:
    """Cells whose accumulation exceeds the threshold are stream cells."""
    if threshold is None:
        threshold = settings.hydrology.stream_threshold

    streams = acc >= threshold
    stream_count = int(np.sum(streams))
    logger.info(f"Stream detection: {stream_count} stream cells (threshold={threshold})")
    return streams
