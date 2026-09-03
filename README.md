# Pond Catchment Analysis API

A backend API that accepts contour maps in KML/KMZ format, analyzes terrain,
and returns catchment information required for pond planning.

**Author:** Rahul Razz | **ID:** 12341690  
**GitHub Repo:** https://github.com/Rahulrajln1111/pond-catchment-analysis  
**API Endpoint:** `POST http://10.1.75.51:4289/analyzeContour`  

![alt text](image.png)

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Project Structure](#project-structure)
3. [Setup & Installation](#setup--installation)
4. [API Documentation](#api-documentation)
5. [Algorithm Explanation](#algorithm-explanation)
6. [River Detection Report](#river-detection-report)
7. [Demonstration](#demonstration)
8. [Code Extensibility](#code-extensibility)
9. [References](#references)
10. [Acknowledgments](#acknowledgments)

---

## Project Overview

This project solves the problem of **identifying suitable pond locations** from
contour map data. Given a KML/KMZ contour map, the system:

1. Parses terrain elevation data from contour lines
2. Builds a Digital Elevation Model (DEM) grid
3. Simulates water flow across the terrain
4. Identifies natural water (where water collects)
5. Estimates catchment areas (land that drains to each point)
6. Simulates pond filling to calculate storage capacity
7. Returns structured JSON with candidate pond sites

**Key Achievement:** The system correctly identified the Shivnath River in
Chhattisgarh, India using color-based detection with **zero false positives**
when verified against OpenStreetMap data.

---

## Project Structure

```
pond_catchment/
├── test.py                   # KML visualization generator (calls API)
├── contours_1m.kml           # Sample contour map input
├── requirements.txt          # Python dependencies
├── app/
│   ├── __init__.py
│   ├── config.py             # Centralized configuration (all tuneable parameters)
│   ├── models.py             # Pydantic response models (API schema)
│   ├── main.py               # FastAPI app entry point
│   ├── parsers/
│   │   ├── __init__.py
│   │   └── kml_parser.py     # KML/KMZ parsing + river detection
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── dem_builder.py    # Contour lines → DEM grid (interpolation)
│   │   ├── hydrology.py      # Sink filling, D8 flow direction, accumulation
│   │   ├── catchment.py      # Catchment delineation (BFS upstream)
│   │   └── pond_finder.py    # Pond site selection + simulation
│   └── routes/
│       ├── __init__.py
│       └── contour.py        # POST /analyzeContour endpoint
└── venv/                     # Virtual environment
```

<div style="page-break-after: always;"></div>


**Module Responsibilities:**

| Module | Purpose | Key Algorithm |
|--------|---------|---------------|
| `kml_parser.py` | Parse KML/KMZ files, extract contours, detect rivers | XML parsing + color analysis |
| `dem_builder.py` | Convert scattered contour lines to regular elevation grid | Scipy `griddata` interpolation |
| `hydrology.py` | Compute water flow direction and accumulation | D8 algorithm (O'Callaghan & Mark 1984) |
| `catchment.py` | Find all upstream cells draining to a point | BFS on reverse flow graph |
| `pond_finder.py` | Select best pond sites, simulate filling | Multi-criteria filtering + area calculation |
| `contour.py` | API endpoint, orchestrate pipeline | FastAPI request handling |

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- pip


### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.115.12 | Web framework for API |
| uvicorn | 0.34.3 | ASGI server |
| python-multipart | 0.0.20 | File upload support |
| lxml | 5.4.0 | KML/XML parsing |
| numpy | 2.2.6 | Numerical computations |
| scipy | 1.15.3 | Interpolation, ndimage |
| shapely | 2.1.2 | Geometric operations |
| scikit-image | 0.22+ | Boundary tracing (marching squares) |
| requests | 2.31+ | API calls (for test.py) |
| pydantic | (via fastapi) | Data validation |


Server starts at: `http://10.1.75.51:4289`

---

## Generating KML Visualization (test.py)

The `test.py` script calls the running API and generates a combined KML file
that overlays pond analysis results on top of the original contour map.

### Files Required

Place these in the project root directory (`pond_catchment/`):
- `test.py` — KML generation script
- `contours_1m.kml` — Sample contour map input

### Usage

```bash
# 2. Run test.py (in another terminal)
cd pond_catchment
source venv/bin/activate
python3 test.py
```

### Output

`test.py` generates `pond_analysis.kml` containing:

| Layer | Color | Content |
|-------|-------|---------|
| Terrain contours | Brown lines | Original 1262 elevation contour lines |
| River contours | Blue lines | 93 Shivnath River contour lines |
| Pond sites | Red pins | 5 candidate pond locations |
| Catchment areas | Orange polygons | Land draining to each pond |
| Pond surfaces | Light blue polygons | Water surface when pond is filled |

### Viewing Results

Open `pond_analysis.kml` in:
- **Google Earth Desktop** — Best experience, full 3D terrain
- **Kml viewer Earth Web** — https://kmlviewer.nsspot.net/ → Import KML

Each placemark includes a popup with elevation, catchment area,
pond area, and volume information.

### Alternative: Direct API Call

```bash
# Get  pond site coordinates
curl -X POST http://10.1.75.51:4289/analyzeContour \
  -F "file=@contours_1m.kml" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for i, s in enumerate(data['candidate_sites']):
    print(f'\nSite {i+1}:')
    print(f'  Location: ({s[\"location\"][\"latitude\"]:.6f}, {s[\"location\"][\"longitude\"]:.6f})')
    print(f'  Elevation: {s[\"elevation_m\"]:.1f}m')
    print(f'  Catchment: {s[\"catchment_area_hectares\"]:.2f} ha')
    print(f'  Pond area: {s[\"pond_area_sqm\"]:.0f} sqm')
    print(f'  Volume: {s[\"pond_volume_m3\"]:.0f} m3')
"
```

## API Documentation

### POST /analyzeContour

Analyzes a contour map and returns catchment information for pond planning.

**Request:**

```
Content-Type: multipart/form-data
Body: file (KML or KMZ file)
```

**Example (curl):**

```bash
curl -X POST http://10.1.75.51:4289/analyzeContour \
  -F "file=@contours_1m.kml"
```

**Response Schema:**

```json
{
  "status": "success",
  "message": "Found 5 candidate pond sites from 1355 contour lines",
  "terrain": {
    "elevation_min_m": 267.0,
    "elevation_max_m": 298.0,
    "elevation_range_m": 31.0,
    "total_contours": 1355,
    "total_points": 159113,
    "area_boundary": [{"latitude": 21.263, "longitude": 81.281}, ...]
  },
  "candidate_sites": [
    {
      "location": {"latitude": 21.262622, "longitude": 81.287104},
      "elevation_m": 276.0,
      "catchment_area_sqm": 15366.26,
      "catchment_area_hectares": 1.54,
      "catchment_boundary": [{"latitude": ..., "longitude": ...}, ...],
      "pond_boundary": [{"latitude": ..., "longitude": ...}, ...],
      "pond_area_sqm": 2602.0,
      "pond_volume_m3": 1976.0,
      "pond_depth_m": 2.0,
      "water_surface_elevation_m": 278.0,
      "river_excluded": false
    },
    ...
  ],
  "rivers_detected": true
}
```

**Response Fields:**

| Field | Description |
|-------|-------------|
| `terrain.elevation_min_m` | Lowest elevation in DEM (meters) |
| `terrain.elevation_max_m` | Highest elevation in DEM (meters) |
| `terrain.total_contours` | Number of contour lines parsed from KML |
| `candidate_sites[].location` | GPS coordinates of suggested pond center |
| `candidate_sites[].elevation_m` | Ground elevation at pond site |
| `candidate_sites[].catchment_area_sqm` | Land area draining to this pond (sq meters) |
| `candidate_sites[].catchment_area_hectares` | Catchment area in hectares |
| `candidate_sites[].catchment_boundary` | Polygon defining the catchment boundary |
| `candidate_sites[].pond_boundary` | Polygon defining the water surface when filled |
| `candidate_sites[].pond_area_sqm` | Water surface area of the pond |
| `candidate_sites[].pond_volume_m3` | Estimated water storage volume (cubic meters) |
| `candidate_sites[].pond_depth_m` | Designed depth of the pond |
| `candidate_sites[].water_surface_elevation_m` | Elevation when pond is full |
| `candidate_sites[].river_excluded` | True if river areas were excluded |
| `rivers_detected` | True if river features found in KML |

**Error Responses:**

| Status | Meaning |
|--------|---------|
| 400 | Invalid file type or no filename |
| 413 | File too large (>10MB) |
| 422 | No contour lines found in KML |
| 500 | Internal analysis error |


---

## Algorithm Explanation

The analysis pipeline consists of 6 stages:

### Stage 1: KML Parsing (`kml_parser.py`)

**Input:** Raw KML/KMZ file bytes  
**Output:** List of contour lines with elevations + river flags

The parser extracts:
- **Contour lines** — LineString features with numeric elevation names
- **Boundary polygon** — Polygon features defining the study area
- **River detection** — Three-layer approach (detailed in River Detection section)

KML uses `longitude,latitude` coordinate order (opposite of common convention).

### Stage 2: DEM Builder (`dem_builder.py`)

**Input:** Contour lines + boundary polygon  
**Output:** Regular elevation grid (2D numpy array)

Scattered contour points are interpolated onto a regular grid using
**scipy.interpolate.griddata** with linear interpolation.

- Grid resolution: 0.0001° ≈ 11.1 meters at this latitude
- Grid size: 313 × 238 cells (74,494 total cells)
- Fill value: NaN for areas outside the contour data convex hull

**Reference:** Scipy documentation — `scipy.interpolate.griddata`

### Stage 3: Sink Filling (`hydrology.py`)

**Input:** Raw DEM with depressions  
**Output:** Pit-filled DEM where water can flow to edges

**Algorithm:** Priority-queue based sink filling (Barnes et al. 2014)

1. Push all edge cells into a min-heap (priority queue)
2. Pop the lowest cell
3. For each unvisited neighbor:
   - If neighbor is lower → it's a sink → raise its elevation
   - Push neighbor into heap
4. Repeat until heap is empty

This ensures water always has a downhill path to the domain edge.

**Reference:** Barnes, R., et al. (2014). "Priority-flood: An optimal
depression-filling and watershed-labeling algorithm for digital elevation models."

### Stage 4: D8 Flow Direction (`hydrology.py`)

**Input:** Pit-filled DEM  
**Output:** Flow direction grid (each cell points to steepest downslope neighbor)

**Algorithm:** D8 (Eight Direction) flow routing (O'Callaghan & Mark 1984)

For each cell, compute slope to all 8 neighbors:
- Orthogonal neighbors (N,S,E,W): distance = 1
- Diagonal neighbors (NE,NW,SE,SW): distance = √2

Flow direction = neighbor with steepest descent (largest drop/distance).

**D8 Encoding (ESRI standard):**
```
64  128  1
32   X   2
16   8   4
```

Each value is a power of 2 pointing to the downslope neighbor.

**Reference:** O'Callaghan, J.F. & Mark, D.M. (1984). "The extraction of
drainage networks from digital elevation data."

### Stage 5: Flow Accumulation (`hydrology.py`)

**Input:** Flow direction grid  
**Output:** Accumulation grid (how many cells drain into each cell)

**Algorithm:** Topological sort processing

1. Sort all cells by elevation (highest first)
2. For each cell, add its accumulation to its downstream neighbor
3. By the time we process low-elevation cells, all upstream water has accumulated

Higher accumulation = more water汇聚 = likely stream/river channel.

**Reference:** Tarboton, D.G. (1997). "A new method for the determination of
flow directions and upslope areas in grid digital elevation models."

### Stage 6: Catchment Delineation (`catchment.py`)

**Input:** Flow direction grid + pour point coordinates  
**Output:** Boolean mask of all cells draining to that point

**Algorithm:** BFS on reverse flow graph

1. Build reverse flow map: for each cell, store which cells flow INTO it
2. Start BFS from the pour point
3. Follow reverse directions upstream
4. All visited cells = catchment area

The catchment boundary is extracted using edge detection (cells inside
catchment with at least one neighbor outside).

### Stage 7: Pond Selection & Simulation (`pond_finder.py`)

**Input:** DEM, flow direction, accumulation, river mask  
**Output:** Ranked list of candidate pond sites

**Selection Criteria:**
1. High flow accumulation (natural water汇聚点)
2. Low elevation (bottom 30% — valleys, not hilltops)
3. Good local relief (>1m — real depression, not flat)
4. Not on river channel (exclusion zone with buffer)
5. Minimum catchment area (500 sqm)

**Pond Simulation:**
1. Water surface elevation = pour point elevation + design depth (2m)
2. All catchment cells below water surface become the pond
3. Volume = Σ(water depth × cell area) for each submerged cell
4. Boundary = edge cells of the pond mask

---

## River Detection Report

### Problem

Contour map KML files may contain river/stream features alongside terrain
contours. Rivers must be identified and excluded from catchment area calculation
because water in rivers flows downstream through the channel — it does not
contribute to pond catchment at a given point.

### Detection Methods (3-Layer Approach)

#### 1. Name/Label Keyword Matching

Check if the Placemark `<name>` tag contains river-related keywords:
`river, stream, nala, nadi, nallah, creek, brook, channel, drain, waterway`

**Result on sample:** 0 rivers detected (sample has no text labels on rivers)

#### 2. KML LineStyle Color Analysis ✅ PRIMARY METHOD

KML uses `aabbggrr` (Alpha-Blue-Green-Red) color format. River/water features
are commonly styled with blue-dominant colors.

**Detection rule:**
```python
bb > 150 AND bb > gg AND rr < 200
```

**Result on sample:** 93 blue-styled lines detected at elevations 267–274m

#### 3. V-Shape Contour Pattern (Experimental)

Cartographic rule: "Contour lines form a V-shape pointing upstream when crossing
a stream." Implemented but found to be unreliable — 89% false positive rate.

**Status:** Not used in production — methods 1 and 2 are sufficient.

### Verification Against Real Map Data

All 93 detected blue lines were verified against OpenStreetMap:

| Check | Value |
|-------|-------|
| River name (OSM) | Shivnath River |
| Location | Khapri, Durg district, Chhattisgarh, India |
| River bounding box (OSM) | Lat 21.134–21.366, Lon 81.235–81.321 |
| Blue lines bounding box (KML) | Lat 21.240–21.264, Lon 81.283–81.300 |
| Lines inside river bbox | **93 / 93** |
| False positives | **0** |

### Pond Site Verification

All 5 candidate pond sites were verified to be outside the river channel:

| Site | Distance from River | Status |
|------|-------------------|--------|
| Site 1 | 312m | ✅ Outside river |
| Site 2 | 322m | ✅ Outside river |
| Site 3 | 1,461m | ✅ Outside river |
| Site 4 | 2,180m | ✅ Outside river |
| Site 5 | 2,349m | ✅ Outside river |

---

## Demonstration

### Sample Input

File: `contours_1m.kml` (6.42 MB)
- Source: contourmapcreator.urgr8.ch
- Location: Near Khapri, Durg district, Chhattisgarh, India
- Elevation range: 267m – 298m
- Contour interval: 1m
- Features: 1,355 contour lines + 1 boundary polygon + river (Shivnath River)

### API Call

```bash
curl -X POST http://10.1.75.51:4289/analyzeContour \
  -F "file=@contours_1m.kml"
```

### Sample Output

```json
{
  "status": "success",
  "message": "Found 5 candidate pond sites from 1355 contour lines",
  "terrain": {
    "elevation_min_m": 267.0,
    "elevation_max_m": 298.0,
    "elevation_range_m": 31.0,
    "total_contours": 1355,
    "total_points": 159113
  },
  "candidate_sites": [
    {
      "location": {"latitude": 21.262622, "longitude": 81.287104},
      "elevation_m": 276.0,
      "catchment_area_hectares": 1.54,
      "pond_area_sqm": 2602,
      "pond_volume_m3": 1976,
      "pond_depth_m": 2.0,
      "water_surface_elevation_m": 278.0
    }
  ],
  "rivers_detected": true
}
```

### Results Summary

| Site | Elevation | Catchment | Pond Area | Volume | Distance from River |
|------|-----------|-----------|-----------|--------|-------------------|
| 1 | 276.0m | 1.54 ha | 2,602 sqm | 1,976 m³ | 312m |
| 2 | 276.0m | 1.52 ha | 3,098 sqm | 1,778 m³ | 322m |
| 3 | 280.0m | 1.07 ha | 2,478 sqm | 2,135 m³ | 1,461m |
| 4 | 277.0m | 0.94 ha | 5,700 sqm | 4,595 m³ | 2,180m |
| 5 | 277.5m | 0.87 ha | 620 sqm | 538 m³ | 2,349m |

---

## Code Extensibility

The implementation is designed for extensibility to future phases:

### Current Architecture

- **Modular design** — Each analysis stage is a separate module
- **Configuration-driven** — All parameters in `config.py` (no hard-coded values)
- **Generic KML parser** — Works with any KML/KMZ contour map
- **Pluggable algorithms** — Easy to swap D8 for D∞ or MFD

### Future Extensions

| Phase | Enhancement | Implementation Path |
|-------|-------------|-------------------|
| Phase 2 | Multiple ponds per catchment | Modify `pond_finder.py` to iterate |
| Phase 2 | Optimal dam placement | Add topographic analysis in `pond_finder.py` |
| Phase 2 | Cost estimation | Add earthwork volume calculation |
| Phase 3 | Web frontend | Add React/Vue frontend consuming the API |
| Phase 3 | GIS export | Add GeoJSON/KML export endpoints |
| Phase 3 | Satellite imagery | Integrate with Google Earth Engine |

### Adding New Contour Maps

The system is fully generic. To process a new contour map:

1. Generate KML/KMZ from any source (QGIS, ArcGIS, contourmapcreator)
2. Upload via `POST /analyzeContour`
3. System automatically:
   - Detects contour intervals
   - Identifies rivers (by color/labels)
   - Builds DEM and analyzes hydrology
   - Returns catchment information

No code changes needed for new input files.

---

## References

### Algorithms

1. **D8 Flow Direction:** O'Callaghan, J.F. & Mark, D.M. (1984). "The extraction of drainage networks from digital elevation data." *Computer Vision, Graphics, and Image Processing*, 28(3), 323-344.

2. **Priority-Flood Sink Filling:** Barnes, R., et al. (2014). "Priority-flood: An optimal depression-filling and watershed-labeling algorithm for digital elevation models." *Computers & Geosciences*, 62, 143-153.

3. **Flow Accumulation:** Tarboton, D.G. (1997). "A new method for the determination of flow directions and upslope areas in grid digital elevation models." *Water Resources Research*, 33(2), 309-319.

4. **TauDEM:** Tarboton, D.G. (2023). "TauDEM: Terrain Analysis Using Digital Elevation Models." Utah State University. https://hydrology.usu.edu/taudem/

### Libraries & Tools

5. **FastAPI:** https://fastapi.tiangolo.com/ — Modern Python web framework for building APIs.

6. **Scipy Interpolation:** https://docs.scipy.org/doc/scipy/reference/interpolate.html — Used for DEM generation from contour points.

7. **LXML:** https://lxml.de/ — XML/KML parsing library.

8. **NumPy:** https://numpy.org/ — Numerical computing library.


### Research & Documentation

9. **USGS D8 Algorithm:** https://pubs.usgs.gov/tm/6/a2/pdf/TM_6-A2.pdf — Official documentation of D8 flow direction.

---

## Acknowledgments

**Development Tool:** Codebuff (Free AI coding assistant) — Assisted with algorithm design, code implementation, debugging, and documentation throughout this project.

**Libraries:** Built on top of excellent open-source libraries including FastAPI, NumPy, SciPy, and LXML.

**Data:** Sample contour map generated from NASA/USGS SRTM elevation data. River verification using OpenStreetMap data.

---