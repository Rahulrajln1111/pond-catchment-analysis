from app.parsers.kml_parser import ParsedKML
from app.config import settings
import logging
import numpy as np
from scipy.interpolate import griddata
logger = logging.getLogger(__name__)

def deg_to_meters(degree:float)->float:
    return abs(degree)*111_320


def build_dem(parsed:ParsedKML)->dict:
    all_x = []
    all_y = []
    all_z = []
    
    for contour in parsed.contours:
        for lon , lat in contour.coords:
            all_x.append(lon)
            all_y.append(lat)
            all_z.append(contour.elevation)
    
    all_x = np.array(all_x)
    all_y = np.array(all_y)
    all_z = np.array(all_z)
    
    logger.info(f"Collected {len(all_x)} elevation points from {len(parsed.contours)} contour lines")
    
    if parsed.boundary_coords:
        bnd_long = [c[0] for c in parsed.boundary_coords]
        bnd_lats = [c[1] for c in parsed.boundary_coords]
        
        x_min , x_max = min(bnd_long),max(bnd_long)
        y_min , y_max = min(bnd_lats),max(bnd_lats)
    
    else:
        x_min , x_max = all_x.min(),all_x.max()
        y_min , y_max = all_y.min(),all_y.max()
    
    # Create regular grid
    
    resolution = settings.dem.grid_resolution
    x_grid = np.arange(x_min,x_max,resolution)
    y_grid = np.arange(y_min,y_max,resolution)
    xx,yy = np.meshgrid(x_grid,y_grid)
    
    logger.info(f"DEM grid : {xx.shape[1]} x {xx.shape[0]} cells (resolution : {resolution} degree)")
    dem = griddata(
        points=np.column_stack((all_x,all_y)),
        values=all_z,
        xi=(xx,yy),
        method=settings.dem.interpolation_method,
        fill_value=settings.dem.fill_value,
        )
    transform = {
        "x_min":float(x_min),
        "x_max":float(x_max),
        "y_min":float(y_min),
        "y_max":float(y_max),
        "resolution":resolution,
        "rows":dem.shape[0],
        "cols":dem.shape[1],
        "cell_size_m":deg_to_meters(resolution),
    }
    
    return {
        "dem":dem,
        "transform":transform,
    }