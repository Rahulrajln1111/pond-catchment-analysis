from pydantic import BaseModel, Field
 
 
class Coordinate(BaseModel):
    """A single geographic coordinate."""
 
    latitude: float = Field(..., description="Latitude in decimal degrees")
    longitude: float = Field(..., description="Longitude in decimal degrees")
 
 
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
 
 
class TerrainSummary(BaseModel):
    """Summary of the terrain analysis from the contour map."""
 
    elevation_min_m: float = Field(..., description="Lowest elevation found")
    elevation_max_m: float = Field(..., description="Highest elevation found")
    elevation_range_m: float = Field(..., description="Difference between max and min")
    total_contours: int = Field(..., description="Number of contour lines parsed")
    total_points: int = Field(
        ..., description="Total coordinate points across all contours"
    )
    area_boundary: list[Coordinate] = Field(
        ..., description="Boundary polygon of the study area (if present in KML)"
    )
 
 
class AnalysisResponse(BaseModel):
    """
    Full response from POST /analyzeContour.
 
    This is the top-level schema returned by the API.
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
        description="True if river features were found in the KML"
    )