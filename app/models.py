from pydantic import BaseModel, Field


class Coordinate(BaseModel):
    """A single geographic coordinate."""

    latitude: float = Field(..., description="Latitude in decimal degrees")
    longitude: float = Field(..., description="Longitude in decimal degrees")


class PolygonRings(BaseModel):
    """A polygon expressed as GeoJSON rings of coordinates."""

    rings: list[list[Coordinate]] = Field(
        ...,
        description="Polygon rings; rings[0] is the outer boundary, others are holes",
    )


class PondSite(BaseModel):
    """A single candidate pond location with its catchment info."""

    location: Coordinate = Field(
        ..., description="Suggested pond center point"
    )
    elevation_m: float = Field(
        ..., description="Elevation at the pond site in meters"
    )
    catchment_area_sqm: float = Field(
        ..., description="Total catchment area draining to this pond (sq meters)"
    )
    catchment_area_hectares: float = Field(
        ..., description="Catchment area in hectares (1 hectare = 10,000 sqm)"
    )
    catchment_boundary: list[Coordinate] = Field(
        ..., description="Simplified polygon boundary of the catchment area"
    )
    river_excluded: bool = Field(
        default=False,
        description="True if river areas were excluded from catchment calculation"
    )
    pond_boundary: list[Coordinate] = Field(
        default_factory=list,
        description="Main polygon boundary of the water surface when filled"
    )
    pond_boundary_rings: list[list[Coordinate]] = Field(
        default_factory=list,
        description="All pond water-surface polygons (a pond may have several lobes)"
    )
    pond_area_sqm: float = Field(
        default=0.0,
        description="Water surface area of the pond (sq meters)"
    )
    pond_volume_m3: float = Field(
        default=0.0,
        description="Estimated water storage volume (cubic meters)"
    )
    pond_depth_m: float = Field(
        default=0.0,
        description="Designed depth of the pond (meters)"
    )
    water_surface_elevation_m: float = Field(
        default=0.0,
        description="Elevation of the water surface when full (meters)"
    )


class TerrainSummary(BaseModel):
    """Summary of the terrain analysis."""

    elevation_min_m: float = Field(..., description="Lowest elevation found")
    elevation_max_m: float = Field(..., description="Highest elevation found")
    elevation_range_m: float = Field(..., description="Difference between max and min")
    total_contours: int = Field(
        default=0,
        description="Number of contour lines parsed (0 for drawn-area analysis)",
    )
    total_points: int = Field(
        default=0,
        description="Total coordinate points across all contours",
    )
    area_boundary: list[Coordinate] = Field(
        default_factory=list,
        description="Boundary polygon of the study area",
    )
    grid_cells: int = Field(
        default=0, description="DEM grid cells used for the analysis"
    )
    grid_resolution_m: float = Field(
        default=0.0, description="Approximate DEM cell size in meters"
    )


class AnalysisResponse(BaseModel):
    """
    Full response from the analysis endpoints.

    Shared by POST /analyzeContour (KML upload) and POST /analyzeArea
    (drawn polygon on the map).
    """

    status: str = Field(
        default="success", description="Status of the analysis"
    )
    message: str = Field(
        default="", description="Human-readable summary message"
    )
    terrain: TerrainSummary = Field(
        ..., description="Summary of the input terrain data"
    )
    candidate_sites: list[PondSite] = Field(
        ..., description="Ranked list of suitable pond locations"
    )
    rivers_detected: bool = Field(
        default=False,
        description="True if river features were found in the input",
    )
    data_source: str = Field(
        default="kml-contours",
        description="Source of elevation data: 'kml-contours' or 'aws-terrain-tiles'",
    )
    analysis_time_ms: float = Field(
        default=0.0, description="Total server-side analysis time in milliseconds"
    )
    context_id: str | None = Field(
        default=None,
        description=(
            "Terrain context ID for follow-up requests (POST /analyzeSite, "
            "GET /export/kml). Null for KML-upload analyses."
        ),
    )


# --- Request models for drawn-area analysis ---


class PolygonInput(BaseModel):
    """GeoJSON Polygon or MultiPolygon (coordinates may be [lon, lat])."""

    type: str = Field(..., description="'Polygon' or 'MultiPolygon'")
    coordinates: list = Field(
        ..., description="GeoJSON coordinates of the polygon(s)"
    )


class AreaAnalysisRequest(BaseModel):
    """Request body for POST /analyzeArea (user draws a shape on the map)."""

    polygon: PolygonInput = Field(
        ..., description="Selected land area as GeoJSON Polygon/MultiPolygon"
    )
    target_cell_m: float = Field(
        default=10.0,
        ge=5.0,
        le=60.0,
        description="Desired DEM cell size in meters (smaller = finer but slower)",
    )
    max_sites: int = Field(
        default=5, ge=1, le=10, description="Maximum candidate sites to return"
    )


class CustomSiteRequest(BaseModel):
    """Request body for POST /analyzeSite (user clicks a specific point)."""

    context_id: str = Field(
        ...,
        description="Context ID returned by /analyzeArea — reuses the cached terrain",
    )
    latitude: float = Field(..., description="Clicked point latitude")
    longitude: float = Field(..., description="Clicked point longitude")
    pond_depth_m: float = Field(
        default=2.0, ge=0.5, le=10.0, description="Desired pond depth in meters"
    )
