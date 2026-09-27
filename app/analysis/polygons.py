"""
Lightweight geometry helpers replacing scikit-image (find_contours) and
shapely (simplify) with pure numpy/stdlib implementations.

- find_contours_mask: marching squares on a boolean mask; returns ordered,
  closed rings of (row, col) float coordinates, longest ring first.
  Vertices sit at cell-edge midpoints (level 0.5).
- simplify_ring: Douglas-Peucker simplification for closed rings
  (tolerance in the same units as the ring coordinates).
"""

import numpy as np

# Marching-squares case table. Case index bits: tl=8, tr=4, br=2, bl=1.
# Segments are DIRECTED (start, end) such that the inside (True region)
# always lies on the cross>0 side in (x=col, y=row) coordinates:
#   cross(A->B, P) = (Bx-Ax)(Py-Ay) - (By-Ay)(Px-Ax) > 0
# This gives every boundary point exactly one outgoing segment, so rings
# chain deterministically and always close (holes become reversed loops,
# which is exactly what polygon formats expect).
_SEGMENT_TABLE = {
    1:  [("L", "B")],
    2:  [("B", "R")],
    3:  [("L", "R")],
    4:  [("R", "T")],
    5:  [("R", "T"), ("L", "B")],   # saddle: tr + bl
    6:  [("B", "T")],
    7:  [("L", "T")],
    8:  [("T", "L")],
    9:  [("T", "B")],
    10: [("T", "L"), ("B", "R")],   # saddle: tl + br
    11: [("T", "R")],
    12: [("R", "L")],
    13: [("R", "B")],
    14: [("B", "L")],
}


def find_contours_mask(mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """
    Extract closed boundary rings of a boolean mask via marching squares.

    Returns a list of rings (each a list of (row, col) float tuples),
    sorted longest-first. The ring is NOT explicitly closed (last != first);
    callers close it if needed.
    """
    m = np.asarray(mask, dtype=bool)
    rows, cols = m.shape
    if rows < 2 or cols < 2 or not m.any():
        return []

    # Pad with a False border (like skimage) so regions touching the grid
    # edge still produce a boundary ring.
    P = np.pad(m, 1, constant_values=False)
    prow, pcol = P.shape  # rows + 2, cols + 2

    # Corner sample arrays over 2x2 blocks of the padded grid. Block (i,j):
    #   tl=P[i,j], tr=P[i,j+1], bl=P[i+1,j], br=P[i+1,j+1]
    tl = np.zeros((prow, pcol), dtype=bool)
    tr = np.zeros_like(tl)
    bl = np.zeros_like(tl)
    br = np.zeros_like(tl)
    tl[:prow, :pcol] = P
    tr[:prow, : pcol - 1] = P[:, 1:]
    bl[: prow - 1, :pcol] = P[1:, :]
    br[: prow - 1, : pcol - 1] = P[1:, 1:]

    case = tl.astype(np.uint8) * 8 + tr.astype(np.uint8) * 4 + br.astype(np.uint8) * 2 + bl.astype(np.uint8)

    # Edge-midpoint coordinates per block (in PADDED frame), vectorized
    ii, jj = np.nonzero((case != 0) & (case != 15))
    T = np.stack([ii.astype(float), jj + 0.5], axis=1)
    B = np.stack([ii + 1.0, jj + 0.5], axis=1)
    L = np.stack([ii + 0.5, jj.astype(float)], axis=1)
    R = np.stack([ii + 0.5, jj + 1.0], axis=1)
    pts = {"T": T, "B": B, "L": L, "R": R}

    # Build directed segment map: start point -> end point
    out_map: dict[tuple[float, float], tuple[float, float]] = {}

    cases = case[ii, jj]
    for k in range(len(ii)):
        for a_name, b_name in _SEGMENT_TABLE[int(cases[k])]:
            a = tuple(pts[a_name][k])
            b = tuple(pts[b_name][k])
            out_map[a] = b

    if not out_map:
        return []

    # Chain directed segments into closed loops
    rings: list[list[tuple[float, float]]] = []
    visited: set[tuple[float, float]] = set()
    for start in list(out_map.keys()):
        if start in visited:
            continue
        ring = [start]
        visited.add(start)
        cur = out_map.get(start)
        guard = 0
        while cur is not None and cur != start and guard < 4 * rows * cols:
            ring.append(cur)
            visited.add(cur)
            cur = out_map.get(cur)
            guard += 1
        if cur == start and len(ring) >= 4:
            # Shift back from padded frame to the original grid frame
            rings.append([(r - 1.0, c - 1.0) for r, c in ring])

    rings.sort(key=len, reverse=True)
    return rings


# ---------------------------------------------------------------------------
# Douglas-Peucker simplification
# ---------------------------------------------------------------------------

def _perp_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Perpendicular distance from point p to the line through a-b."""
    (y1, x1), (y2, x2) = a, b
    (yp, xp) = p
    dy, dx = y2 - y1, x2 - x1
    norm = (dy * dy + dx * dx) ** 0.5
    if norm == 0:
        return ((yp - y1) ** 2 + (xp - x1) ** 2) ** 0.5
    return abs(dy * xp - dx * yp + x2 * y1 - y2 * x1) / norm


def _dp_chain(pts: list[tuple[float, float]], chain: list[int], tol: float, keep: set[int]) -> None:
    """Iterative Douglas-Peucker over pts[chain[i]] for i in [0, len(chain))."""
    if len(chain) < 3:
        return
    stack = [(0, len(chain) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        a, b = pts[chain[lo]], pts[chain[hi]]
        dmax, imax = -1.0, -1
        for k in range(lo + 1, hi):
            d = _perp_dist(pts[chain[k]], a, b)
            if d > dmax:
                dmax, imax = d, k
        if dmax > tol:
            keep.add(chain[imax])
            stack.append((lo, imax))
            stack.append((imax, hi))


def simplify_ring(ring: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """
    Simplify a closed ring with Douglas-Peucker.

    The ring is split at two extreme anchor points and each half is
    simplified independently, so the result stays a closed loop.
    Returns a CLOSED ring (last point == first point).
    """
    pts = list(ring)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 5 or tol <= 0:
        return pts + [pts[0]] if pts else pts

    # Anchor points: extremes of (row+col); fall back to (row-col)
    sums = [p[0] + p[1] for p in pts]
    a, b = int(np.argmin(sums)), int(np.argmax(sums))
    if a == b:
        diffs = [p[0] - p[1] for p in pts]
        a, b = int(np.argmin(diffs)), int(np.argmax(diffs))
        if a == b:
            return pts + [pts[0]]

    def chain(i0: int, i1: int) -> list[int]:
        if i0 <= i1:
            return list(range(i0, i1 + 1))
        return list(range(i0, n)) + list(range(0, i1 + 1))

    keep: set[int] = {a, b}
    _dp_chain(pts, chain(a, b), tol, keep)
    _dp_chain(pts, chain(b, a), tol, keep)

    # Emit kept points in ring order starting at a, then close
    order = list(range(a, n)) + list(range(0, a))
    out = [pts[i] for i in order if i in keep]
    out.append(out[0])
    return out
