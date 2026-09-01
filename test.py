import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom

# Call the API
print('Calling API...')
resp = requests.post(
    'http://10.1.75.51:4289/analyzeContour',
    files={'file': ('contours_1m.kml', open('./contours_1m.kml', 'rb'), 'application/vnd.google-earth.kml+xml')}
)
data = resp.json()
print(f'Got {len(data["candidate_sites"])} sites')

# Parse original contour KML
tree = ET.parse('./contours_1m.kml')
root = tree.getroot()
ns = {'kml': 'http://www.opengis.net/kml/2.2'}

# Build combined KML
kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
doc = ET.SubElement(kml, 'Document')
ET.SubElement(doc, 'name').text = 'Contour Map + Pond Catchment Analysis'

# Terrain contour style: thin brown
style_contour = ET.SubElement(doc, 'Style', id='contour')
line_cont = ET.SubElement(style_contour, 'LineStyle')
ET.SubElement(line_cont, 'color').text = '995588aa'
ET.SubElement(line_cont, 'width').text = '1'

# River contour style: blue
style_river = ET.SubElement(doc, 'Style', id='river')
line_riv = ET.SubElement(style_river, 'LineStyle')
ET.SubElement(line_riv, 'color').text = 'ff990000'
ET.SubElement(line_riv, 'width').text = '2'

# Catchment style: orange border, semi-transparent orange fill
style_c = ET.SubElement(doc, 'Style', id='catchment')
line_c = ET.SubElement(style_c, 'LineStyle')
ET.SubElement(line_c, 'color').text = 'ff00a5ff'
ET.SubElement(line_c, 'width').text = '3'
poly_c = ET.SubElement(style_c, 'PolyStyle')
ET.SubElement(poly_c, 'color').text = '4d00a5ff'

# Pond style: blue border, semi-transparent light blue fill
style_p = ET.SubElement(doc, 'Style', id='pond')
line_p = ET.SubElement(style_p, 'LineStyle')
ET.SubElement(line_p, 'color').text = 'ffff0000'
ET.SubElement(line_p, 'width').text = '2'
poly_p = ET.SubElement(style_p, 'PolyStyle')
ET.SubElement(poly_p, 'color').text = '99ff8800'

# Site style: red marker
style_s = ET.SubElement(doc, 'Style', id='site')
line_s = ET.SubElement(style_s, 'LineStyle')
ET.SubElement(line_s, 'color').text = 'ff0000ff'
ET.SubElement(line_s, 'width').text = '2'
poly_s = ET.SubElement(style_s, 'PolyStyle')
ET.SubElement(poly_s, 'color').text = 'ff0000ff'

# Add all contour lines from original KML
print('Adding contour lines...')
placemarks = root.findall('.//kml:Placemark', ns)
contour_count = 0
river_count = 0

for pm in placemarks:
    name = pm.find('kml:name', ns)
    line_string = pm.find('.//kml:LineString', ns)
    if line_string is None:
        continue

    coords_elem = line_string.find('kml:coordinates', ns)
    if coords_elem is None or not coords_elem.text:
        continue

    # Detect river from inline style color (blue: high blue, low red)
    is_river = False
    inline_style = pm.find('kml:Style', ns)
    if inline_style is not None:
        line_color = inline_style.find('.//kml:LineStyle/kml:color', ns)
        if line_color is not None and line_color.text:
            c = line_color.text.strip()
            if len(c) >= 6:
                bb = int(c[0:2], 16)
                gg = int(c[2:4], 16)
                rr = int(c[4:6], 16)
                if bb > 150 and rr < 100 and gg < 100:
                    is_river = True

    contour_pm = ET.SubElement(doc, 'Placemark')
    name_text = name.text if name is not None else f'Contour'
    ET.SubElement(contour_pm, 'name').text = name_text
    ET.SubElement(contour_pm, 'styleUrl').text = '#river' if is_river else '#contour'

    new_ls = ET.SubElement(contour_pm, 'LineString')
    ET.SubElement(new_ls, 'tessellate').text = '1'
    ET.SubElement(new_ls, 'altitudeMode').text = 'clampToGround'
    ET.SubElement(new_ls, 'coordinates').text = coords_elem.text.strip()

    if is_river:
        river_count += 1
    else:
        contour_count += 1

print(f'  Added {contour_count} terrain contours + {river_count} river contours')

# Add pond analysis results
print('Adding pond analysis results...')
for i, site in enumerate(data['candidate_sites']):
    loc = site['location']

    # Pond site point
    pm = ET.SubElement(doc, 'Placemark')
    ET.SubElement(pm, 'name').text = f'Pond Site {i+1}'
    ET.SubElement(pm, 'styleUrl').text = '#site'
    ET.SubElement(pm, 'description').text = (
        f'Elevation: {site["elevation_m"]:.1f}m\n'
        f'Catchment: {site["catchment_area_hectares"]:.2f} ha\n'
        f'Pond area: {site["pond_area_sqm"]:.0f} sqm\n'
        f'Volume: {site["pond_volume_m3"]:.0f} m3'
    )
    point = ET.SubElement(pm, 'Point')
    ET.SubElement(point, 'coordinates').text = f'{loc["longitude"]},{loc["latitude"]},0'

    # Catchment boundary polygon
    if site['catchment_boundary']:
        pm = ET.SubElement(doc, 'Placemark')
        ET.SubElement(pm, 'name').text = f'Catchment {i+1} ({site["catchment_area_hectares"]:.2f} ha)'
        ET.SubElement(pm, 'styleUrl').text = '#catchment'
        poly = ET.SubElement(pm, 'Polygon')
        ET.SubElement(poly, 'tessellate').text = '1'
        ET.SubElement(poly, 'altitudeMode').text = 'clampToGround'
        outer = ET.SubElement(poly, 'outerBoundaryIs')
        ring = ET.SubElement(outer, 'LinearRing')
        coords = ' '.join(f'{p["longitude"]},{p["latitude"]},0' for p in site['catchment_boundary'])
        ET.SubElement(ring, 'coordinates').text = coords

    # Pond boundary polygon
    if site['pond_boundary']:
        pm = ET.SubElement(doc, 'Placemark')
        ET.SubElement(pm, 'name').text = f'Pond {i+1} ({site["pond_area_sqm"]:.0f} sqm, {site["pond_volume_m3"]:.0f} m3)'
        ET.SubElement(pm, 'styleUrl').text = '#pond'
        poly = ET.SubElement(pm, 'Polygon')
        ET.SubElement(poly, 'tessellate').text = '1'
        ET.SubElement(poly, 'altitudeMode').text = 'clampToGround'
        outer = ET.SubElement(poly, 'outerBoundaryIs')
        ring = ET.SubElement(outer, 'LinearRing')
        coords = ' '.join(f'{p["longitude"]},{p["latitude"]},0' for p in site['pond_boundary'])
        ET.SubElement(ring, 'coordinates').text = coords

# Write KML
xml_str = ET.tostring(kml, encoding='unicode')
pretty = minidom.parseString(xml_str).toprettyxml(indent='  ')
with open('./pond_analysis.kml', 'w') as f:
    f.write(pretty)

print(f'\nSaved ./pond_analysis.kml')
print(f'  - {contour_count} terrain contours (brown lines)')
print(f'  - {river_count} river contours (blue lines)')
print(f'  - {len(data["candidate_sites"])} pond sites (red pins)')
print(f'  - {len(data["candidate_sites"])} catchment polygons (orange)')
print(f'  - {len(data["candidate_sites"])} pond polygons (light blue)')
print('Open in Google Earth')
