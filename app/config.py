"""
Centralized configuration for the catchment analysis pipeline.
 
All tuneable parameters live here so they can be adjusted without
touching algorithmic code. In future phases, these can be loaded
from environment variables or a config file.
"""
 
from dataclasses import dataclass, field
 
 
@dataclass(frozen=True)
class DEMConfig:
    """Configuration for DEM (Digital Elevation Model) generation."""
 
    # Resolution of the output DEM grid in degrees.
    # ~0.00001° ≈ 1 meter at the equator
    grid_resolution: float = 0.0001
 
    # Interpolation method for scipy.interpolate.griddata.
    # "linear" is fast and faithful to contour lines.
    interpolation_method: str = "linear"
 
    # Fill value for areas outside the contour data convex hull.
    fill_value: float = float("nan")
 
 
@dataclass(frozen=True)
class HydrologyConfig:
    """Configuration for hydrological analysis."""
 
    # D8 flow direction encoding (standard USDA/ESRI convention):
    #   64  128  1
    #   32   X   2
    #   16   8   4
 
    # Minimum flow accumulation (in cells) to classify as a stream.
    # Higher = fewer streams detected. Lower = more streams.
    stream_threshold: int = 50
 
    # Buffer width (in cells) around streams for exclusion zones.
    stream_buffer_cells: int = 5
 
    # Minimum area (sq meters) for a stream to be classified as a "river"
    # (rivers are excluded from catchment area).
    min_river_area_sqm: float = 1000.0
 
 
@dataclass(frozen=True)
class PondConfig:
    """Configuration for pond site selection."""
 
    # Minimum catchment area (sq meters) for a viable pond.
    min_catchment_area_sqm: float = 500.0
 
    # Maximum candidate pond sites to return.
    max_candidates: int = 5
 
    # Pond sites should be in the lower X% of elevations (valleys).
    elevation_percentile_threshold: float = 0.3
 
    # Minimum local relief (meters) — ensures real depression, not flat plain.
    min_local_relief: float = 1.0
 
 
@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""
 
    dem: DEMConfig = field(default_factory=DEMConfig)
    hydrology: HydrologyConfig = field(default_factory=HydrologyConfig)
    pond: PondConfig = field(default_factory=PondConfig)
 
    # Keywords that identify river/stream features in KML placemark names.
    # Used to exclude rivers from catchment calculation.
    river_keywords: tuple[str, ...] = (
        "river", "stream", "nala", "nadi", "nallah",
        "creek", "brook", "channel", "drain", "waterway",
    )
 
    # Supported file extensions
    allowed_extensions: tuple[str, ...] = (".kml", ".kmz")
 
    # Maximum upload file size (10 MB)
    max_upload_size_bytes: int = 10 * 1024 * 1024
 
 
# Singleton config — import this wherever you need config values.
settings = AppConfig()