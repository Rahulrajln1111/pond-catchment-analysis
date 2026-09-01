import logging
from heapq import heappush,heappop

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

D8_CODES = {
    1:   (0, 1),    # East
    2:   (1, 1),    # Southeast
    4:   (1, 0),    # South
    8:   (1, -1),   # Southwest
    16:  (0, -1),   # West
    32:  (-1, -1),  # Northwest
    64:  (-1, 0),   # North
    128: (-1, 1),   # Northeast
}

DIAG_FACTOR = 1.41421356237 # sqrt(2) 

def fill_sinks(dem: np.ndarray) -> np.ndarray:
    rows, cols = dem.shape
    filled = dem.copy()
    visited = np.zeros((rows, cols), dtype=bool)
 
    heap = []
 
    # Step 1: Push all edge cells into the heap
    for r in range(rows):
        for c in range(cols):
            if np.isnan(filled[r, c]):
                continue
            if r == 0 or r == rows - 1 or c == 0 or c == cols - 1:
                heappush(heap, (filled[r, c], r, c))
                visited[r, c] = True
 
    # Step 2: Process heap — fill sinks
    while heap:
        elev, r, c = heappop(heap)
 
        for code, (dr, dc) in D8_CODES.items():
            nr, nc = r + dr, c + dc
 
            # Bounds check
            if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                continue
            if visited[nr, nc]:
                continue
            if np.isnan(filled[nr, nc]):
                visited[nr, nc] = True
                continue
 
            visited[nr, nc] = True
 
            # If neighbor is lower than current cell → it's a sink
            if filled[nr, nc] < elev:
                filled[nr, nc] = elev  # Fill the sink
 
            heappush(heap, (filled[nr, nc], nr, nc))
 
    filled_count = np.sum(~np.isnan(filled))
    logger.info(f"Sink filling complete: {filled_count} cells processed")
    return filled


def compute_flow_direction(dem: np.ndarray) -> np.ndarray:

    rows, cols = dem.shape
    fdir = np.zeros((rows, cols), dtype=np.int32)
 
    for r in range(rows):
        for c in range(cols):
            if np.isnan(dem[r, c]):
                continue
 
            max_drop = 0.0
            best_code = 0
 
            for code, (dr, dc) in D8_CODES.items():
                nr, nc = r + dr, c + dc
 
                if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                    continue
                if np.isnan(dem[nr, nc]):
                    continue
 
                # Calculate drop
                drop = dem[r, c] - dem[nr, nc]
 
                # Distance factor: diagonal = sqrt(2), orthogonal = 1
                dist = DIAG_FACTOR if (dr != 0 and dc != 0) else 1.0
                
                slope = drop / dist
 
                if slope > max_drop:
                    max_drop = slope
                    best_code = code
 
            fdir[r, c] = best_code
 
    flow_cells = np.sum(fdir > 0)
    logger.info(f"Flow direction computed: {flow_cells} cells have outgoing flow")
    return fdir


def compute_flow_accumulation(fdir: np.ndarray, dem: np.ndarray) -> np.ndarray:
    rows, cols = fdir.shape
    acc = np.ones((rows, cols), dtype=np.float64)
 
    # Create list of (elevation, row, col) for all valid cells
    cells = []
    for r in range(rows):
        for c in range(cols):
            if not np.isnan(dem[r, c]):
                cells.append((dem[r, c], r, c))
 
    # Sort by elevation — highest first (water flows downhill)
    cells.sort(reverse=True)
 
    # Process each cell: add its accumulation to downstream neighbor
    for elev, r, c in cells:
        code = fdir[r, c]
        if code == 0:
            continue  # No outgoing flow (outlet/edge)
 
        dr, dc = D8_CODES[code]
        nr, nc = r + dr, c + dc
 
        if 0 <= nr < rows and 0 <= nc < cols:
            acc[nr, nc] += acc[r, c]
 
    max_acc = np.nanmax(acc)
    logger.info(
        f"Flow accumulation computed: max={max_acc:.0f}, "
        f"mean={np.nanmean(acc):.1f}"
    )
    return acc


def detect_streams(fdir: np.ndarray,acc: np.ndarray,threshold: int | None = None,) -> np.ndarray:
    if threshold is None:
        threshold = settings.hydrology.stream_threshold
 
    streams = acc >= threshold
    stream_count = np.sum(streams)
    logger.info(
        f"Stream detection: {stream_count} stream cells "
        f"(threshold={threshold})"
    )
    return streams

