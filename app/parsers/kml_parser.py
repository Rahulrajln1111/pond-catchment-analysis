import logging
import io
import zipfile
import xml.etree.ElementTree as etree
from dataclasses import dataclass, field

from app.config import settings

KML_NS = "http://www.opengis.net/kml/2.2"

logger = logging.getLogger(__name__)


@dataclass
class ContourLine:
    elevation: float
    coords: list[tuple[float, float]]
    placemark_id: str = ""
    is_river: bool = False


@dataclass
class ParsedKML:
    contours: list[ContourLine] = field(default_factory=list)
    boundary_coords: list[tuple[float, float]] = field(default_factory=list)
    river_contours: list[ContourLine] = field(default_factory=list)


def parse_coordinate(coord_text: str) -> list[tuple[float, float]]:
    coords = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            lon = float(parts[0])
            lat = float(parts[1])
            coords.append((lon, lat))
    return coords


def parse_elevation(name_text: str | None) -> float | None:
    if not name_text:
        return None
    try:
        return float(name_text.strip())
    except ValueError:
        logger.debug(f"Could not parse elevation from name: '{name_text}'")
        return None


def is_river_feature(name: str) -> bool:
    if not name:
        return False
    name_lower = name.lower()
    return any(kw in name_lower for kw in settings.river_keywords)


def has_blue_style(pm) -> bool:
    """River detection via KML LineStyle color (aabbggrr): blue-dominant."""
    for style in pm.findall(f".//{{{KML_NS}}}LineStyle/{{{KML_NS}}}color"):
        if style.text:
            color = style.text.strip().lstrip("#")
            if len(color) == 8:
                bb = int(color[2:4], 16)
                gg = int(color[4:6], 16)
                rr = int(color[6:8], 16)
                if bb > 150 and bb > gg and rr < 200:
                    return True
    return False


def extract_contours(root) -> list[ContourLine]:
    contours: list[ContourLine] = []
    placemarks = root.findall(f".//{{{KML_NS}}}Placemark")
    logger.info(f"Found {len(placemarks)} total Placemarks in KML")

    for pm in placemarks:
        name_elem = pm.find(f"{{{KML_NS}}}name")
        name_text = name_elem.text if name_elem is not None else ""

        ls = pm.find(f".//{{{KML_NS}}}LineString")
        if ls is None:
            continue

        coord_elem = ls.find(f"{{{KML_NS}}}coordinates")
        if coord_elem is None or not coord_elem.text:
            continue

        elevation = parse_elevation(name_text)
        if elevation is None:
            logger.warning(f"Skipping placemark with no valid elevation: '{name_text}'")
            continue

        coords = parse_coordinate(coord_elem.text)
        if len(coords) < 2:
            logger.warning(f"Skipping contour with only {len(coords)} points")
            continue

        contours.append(ContourLine(
            elevation=elevation,
            coords=coords,
            placemark_id=name_text,
            is_river=is_river_feature(name_text),
        ))

    logger.info(f"Extracted {len(contours)} contour lines")
    return contours


def extract_boundary(root) -> list[tuple[float, float]]:
    polygons = root.findall(f".//{{{KML_NS}}}Polygon")

    for poly in polygons:
        outer = poly.find(f".//{{{KML_NS}}}outerBoundaryIs/{{{KML_NS}}}LinearRing")
        if outer is None:
            continue
        coord_elem = outer.find(f"{{{KML_NS}}}coordinates")
        if coord_elem is None or not coord_elem.text:
            continue

        coords = parse_coordinate(coord_elem.text)
        if len(coords) >= 3:
            logger.info(f"Found boundary polygon with {len(coords)} vertices")
            return coords

    logger.info("No boundary polygon found in KML")
    return []


def read_kml_bytes(file_bytes: bytes, filename: str) -> bytes:
    if filename.lower().endswith(".kmz"):
        logger.info("Detected KMZ file, extracting KML from archive")
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError("KMZ archive does not contain any .kml file")
            with zf.open(kml_names[0]) as kml_file:
                return kml_file.read()
    return file_bytes


def parse_kml(file_bytes: bytes, filename: str) -> ParsedKML:
    logger.info(f"Parsing file: {filename} ({len(file_bytes)} bytes)")

    kml_bytes = read_kml_bytes(file_bytes, filename)
    root = etree.fromstring(kml_bytes)
    all_contours = extract_contours(root)

    # Color-based river detection — mark but keep in the main list
    placemarks = root.findall(f".//{{{KML_NS}}}Placemark")
    pm_by_name = {}
    for pm in placemarks:
        name_elem = pm.find(f"{{{KML_NS}}}name")
        if name_elem is not None and name_elem.text:
            pm_by_name[name_elem.text.strip()] = pm

    river_count = 0
    for c in all_contours:
        pm = pm_by_name.get(c.placemark_id)
        if pm is not None and has_blue_style(pm):
            c.is_river = True
            river_count += 1

    logger.info(f"Marked {river_count} river contours (kept in main list)")

    boundary = extract_boundary(root)

    return ParsedKML(
        contours=all_contours,
        boundary_coords=boundary,
    )
