// Shared types mirroring the FastAPI response schema (app/models.py)

export interface Coordinate {
  latitude: number
  longitude: number
}

export interface PondSite {
  location: Coordinate
  elevation_m: number
  catchment_area_sqm: number
  catchment_area_hectares: number
  catchment_boundary: Coordinate[]
  river_excluded: boolean
  pond_boundary: Coordinate[]
  pond_boundary_rings: Coordinate[][]
  pond_area_sqm: number
  pond_volume_m3: number
  pond_depth_m: number
  water_surface_elevation_m: number
}

export interface TerrainSummary {
  elevation_min_m: number
  elevation_max_m: number
  elevation_range_m: number
  total_contours: number
  total_points: number
  area_boundary: Coordinate[]
  grid_cells: number
  grid_resolution_m: number
}

export interface AnalysisResponse {
  status: string
  message: string
  terrain: TerrainSummary
  candidate_sites: PondSite[]
  rivers_detected: boolean
  river_mask_boundary?: Coordinate[][]
  data_source: string
  analysis_time_ms: number
}

export interface SiteAnalysisResponse {
  status: string
  context_id: string
  site: PondSite
}
