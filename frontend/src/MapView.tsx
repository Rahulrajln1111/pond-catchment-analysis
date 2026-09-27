import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import './leaflet.css' // self-hosted Leaflet CSS (no CDN dependency)
import type { AnalysisResponse, PondSite } from './types'

/* eslint-disable @typescript-eslint/no-explicit-any */

export type DrawTool = 'rectangle' | 'polygon' | null

export interface SelectionGeo {
  type: 'Polygon'
  coordinates: number[][][] // GeoJSON [lon, lat]
}

interface MapViewProps {
  selection: SelectionGeo | null
  onSelection: (g: SelectionGeo | null) => void
  drawTool: DrawTool
  analysis: AnalysisResponse | null
  customSite: PondSite | null
  selectedSite: number | null
  onSelectSite: (i: number | null) => void
  onMapClick?: (lat: number, lng: number) => void
  onToolDone?: () => void
}

const SITE_COLORS = ['#f87171', '#34d399', '#c084fc', '#fbbf24', '#fb923c', '#4ade80', '#60a5fa']

function ringsToLatLng(ring: { latitude: number; longitude: number }[]): L.LatLngExpression[] {
  return ring.map((p) => [p.latitude, p.longitude] as [number, number])
}

function popupHtml(n: number | string, site: PondSite, custom = false): string {
  return `
    <div style="font-family:Segoe UI,sans-serif;min-width:230px">
      <div style="font-weight:700;font-size:14px;margin-bottom:4px">
        ${custom ? '★ Your Selected Site' : `Pond Site ${n}`}
      </div>
      <table style="font-size:12px;line-height:1.6">
        <tr><td style="color:#64748b;padding-right:8px">Elevation</td><td><b>${site.elevation_m.toFixed(1)} m</b></td></tr>
        <tr><td style="color:#64748b">Catchment</td><td><b>${site.catchment_area_hectares.toFixed(2)} ha</b> (${Math.round(site.catchment_area_sqm).toLocaleString()} m²)</td></tr>
        <tr><td style="color:#64748b">Pond area</td><td><b>${Math.round(site.pond_area_sqm).toLocaleString()} m²</b></td></tr>
        <tr><td style="color:#64748b">Volume</td><td><b style="color:#059669">${Math.round(site.pond_volume_m3).toLocaleString()} m³</b></td></tr>
        <tr><td style="color:#64748b">Depth</td><td>${site.pond_depth_m.toFixed(1)} m (WSE ${site.water_surface_elevation_m.toFixed(1)} m)</td></tr>
      </table>
    </div>`
}

export default function MapView(props: MapViewProps) {
  const {
    selection,
    onSelection,
    drawTool,
    analysis,
    customSite,
    selectedSite,
    onSelectSite,
    onMapClick,
  } = props

  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const resultLayerRef = useRef<L.LayerGroup | null>(null)
  const selectionLayerRef = useRef<L.LayerGroup | null>(null)
  const previewLayerRef = useRef<L.LayerGroup | null>(null)

  // latest-props refs used inside one-time map initialization
  const onSelectionRef = useRef(onSelection)
  onSelectionRef.current = onSelection
  const onMapClickRef = useRef(onMapClick)
  onMapClickRef.current = onMapClick
  const onSelectSiteRef = useRef(onSelectSite)
  onSelectSiteRef.current = onSelectSite
  const drawToolRef = useRef<DrawTool>(null)
  const onToolDoneRef = useRef(props.onToolDone)
  onToolDoneRef.current = props.onToolDone

  // polygon drawing session state (refs; preview re-rendered via state)
  const polyVertsRef = useRef<L.LatLng[]>([])
  const rectStartRef = useRef<L.LatLng | null>(null)
  const rectLayerRef = useRef<L.Rectangle | null>(null)
  const [polyVerts, setPolyVerts] = useState<L.LatLng[]>([])
  const [, forceRender] = useState(0)

  // ---- Initialize map once ----
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = L.map(containerRef.current, { center: [21.27, 81.29], zoom: 13 })

    const streets = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors',
    })
    const satellite = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, attribution: 'Esri World Imagery' },
    )
    // Place-name labels overlaid on satellite imagery
    const labels = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, attribution: 'Esri Reference' },
    )
    const satWithLabels = L.layerGroup([satellite, labels])
    satWithLabels.addTo(map)
    L.control.layers({ 'Street Map': streets, 'Satellite + Names': satWithLabels }, undefined, {
      position: 'bottomleft',
    }).addTo(map)

    resultLayerRef.current = L.layerGroup().addTo(map)
    selectionLayerRef.current = L.layerGroup().addTo(map)
    previewLayerRef.current = L.layerGroup().addTo(map)

    // Click handling: drawing tools take priority over custom-site picking
    map.on('click', (e: L.LeafletMouseEvent) => {
      const tool = drawToolRef.current
      if (tool === 'polygon') {
        polyVertsRef.current.push(e.latlng)
        setPolyVerts([...polyVertsRef.current])
        renderPolygonPreview()
        return
      }
      if (tool === null && onMapClickRef.current) {
        onMapClickRef.current(e.latlng.lat, e.latlng.lng)
      }
    })

    mapRef.current = map
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function renderPolygonPreview() {
    const layer = previewLayerRef.current
    const verts = polyVertsRef.current
    if (!layer) return
    layer.clearLayers()
    if (verts.length >= 2) {
      L.polyline(verts as L.LatLngExpression[], { color: '#38bdf8', weight: 2, dashArray: '6 4' }).addTo(layer)
    }
    verts.forEach((v) => {
      L.circleMarker(v, { radius: 4, color: '#38bdf8', fillColor: '#38bdf8', fillOpacity: 1 }).addTo(layer)
    })
  }

  function commitPolygon() {
    const verts = polyVertsRef.current
    if (verts.length < 3) return
    const ring = verts.map((v) => [v.lng, v.lat] as number[])
    ring.push(ring[0])
    clearPreview()
    onSelectionRef.current({ type: 'Polygon', coordinates: [ring] })
    onToolDoneRef.current?.() // one-shot: exit draw mode after finishing
  }

  function clearPreview() {
    polyVertsRef.current = []
    rectStartRef.current = null
    rectLayerRef.current = null
    previewLayerRef.current?.clearLayers()
    setPolyVerts([])
  }

  // ---- Bind/unbind rectangle-drag handlers when the tool changes ----
  useEffect(() => {
    drawToolRef.current = drawTool
    const map = mapRef.current
    if (!map) return

    clearPreview()

    if (drawTool === null) {
      map.dragging.enable()
      ;(map as any).off('mousedown', rectMouseDown)
      ;(map as any).off('mousemove', rectMouseMove)
      ;(map as any).off('mouseup', rectMouseUp)
      return
    }

    if (drawTool === 'rectangle') {
      map.dragging.disable()
      ;(map as any).on('mousedown', rectMouseDown)
      ;(map as any).on('mousemove', rectMouseMove)
      ;(map as any).on('mouseup', rectMouseUp)
    } else {
      map.dragging.enable()
    }

    function rectMouseDown(e: any) {
      rectStartRef.current = e.latlng
      rectLayerRef.current?.remove()
      rectLayerRef.current = L.rectangle(L.latLngBounds(e.latlng, e.latlng), {
        color: '#38bdf8', weight: 2, fillOpacity: 0.08,
      }).addTo(previewLayerRef.current!)
    }

    function rectMouseMove(e: any) {
      if (!rectStartRef.current || !rectLayerRef.current) return
      rectLayerRef.current.setBounds(L.latLngBounds(rectStartRef.current, e.latlng))
    }

    function rectMouseUp(e: any) {
      const start = rectStartRef.current
      if (!start) return
      const bounds = L.latLngBounds(start, e.latlng)
      if (Math.abs(bounds.getNorth() - bounds.getSouth()) < 1e-5) return
      const sw = bounds.getSouthWest()
      const ne = bounds.getNorthEast()
      const ring: number[][] = [
        [sw.lng, sw.lat], [ne.lng, sw.lat], [ne.lng, ne.lat], [sw.lng, ne.lat], [sw.lng, sw.lat],
      ]
      rectStartRef.current = null
      previewLayerRef.current?.clearLayers()
      rectLayerRef.current = null
      onSelectionRef.current({ type: 'Polygon', coordinates: [ring] })
      onToolDoneRef.current?.() // one-shot: exit draw mode after one rectangle
    }
  }, [drawTool])

  // ---- Render selection outline ----
  useEffect(() => {
    const layer = selectionLayerRef.current
    if (!layer) return
    layer.clearLayers()
    if (!selection) return
    const latlngs = selection.coordinates[0].map((c) => [c[1], c[0]] as [number, number])
    L.polyline(latlngs, { color: '#38bdf8', weight: 2, dashArray: '6 4' }).addTo(layer)
  }, [selection])

  // ---- Render analysis results ----
  useEffect(() => {
    const layer = resultLayerRef.current
    if (!layer) return
    layer.clearLayers()

    if (analysis) {
      if (analysis.terrain.area_boundary.length > 0) {
        const pts = analysis.terrain.area_boundary.map(
          (p) => [p.latitude, p.longitude] as [number, number],
        )
        L.polyline(pts, { color: '#94a3b8', weight: 1.5, dashArray: '4 4' }).addTo(layer)
      }

      analysis.candidate_sites.forEach((site, i) => {
        const color = SITE_COLORS[i % SITE_COLORS.length]
        const isSel = selectedSite === i

        if (site.catchment_boundary.length > 2) {
          L.polygon(ringsToLatLng(site.catchment_boundary), {
            color,
            weight: isSel ? 3 : 1.5,
            fillOpacity: isSel ? 0.25 : 0.1,
            dashArray: '2 3',
          })
            .bindTooltip(`Catchment ${i + 1}: ${site.catchment_area_hectares.toFixed(2)} ha`, { sticky: true })
            .addTo(layer)
        }

        site.pond_boundary_rings.forEach((ring) => {
          if (ring.length > 2) {
            L.polygon(ringsToLatLng(ring), {
              color: '#7dd3fc',
              weight: isSel ? 2.5 : 1.5,
              fillColor: '#0ea5e9',
              fillOpacity: isSel ? 0.8 : 0.55,
            })
              .bindTooltip(
                `Pond ${i + 1}: ${site.pond_area_sqm.toLocaleString()} m², ${site.pond_volume_m3.toLocaleString()} m³`,
                { sticky: true },
              )
              .addTo(layer)
          }
        })

        const marker = L.circleMarker([site.location.latitude, site.location.longitude], {
          radius: isSel ? 9 : 6,
          color: '#fff',
          weight: 2,
          fillColor: color,
          fillOpacity: 1,
        })
          .bindPopup(popupHtml(i + 1, site))
          .bindTooltip(`Pond Site ${i + 1}`, {
            permanent: true,
            direction: 'right',
            offset: [10, 0],
            className: 'site-label',
          })
          .addTo(layer)
        marker.on('click', () => onSelectSiteRef.current(i))
      })
    }

    if (customSite) {
      const site = customSite
      if (site.catchment_boundary.length > 2) {
        L.polygon(ringsToLatLng(site.catchment_boundary), {
          color: '#fbbf24', weight: 3, fillOpacity: 0.2, dashArray: '2 3',
        })
          .bindTooltip(`Your site: ${site.catchment_area_hectares.toFixed(2)} ha catchment`, { sticky: true })
          .addTo(layer)
      }
      site.pond_boundary_rings.forEach((ring) => {
        if (ring.length > 2) {
          L.polygon(ringsToLatLng(ring), {
            color: '#fbbf24', weight: 2.5, fillColor: '#f59e0b', fillOpacity: 0.75,
          })
            .bindTooltip(
              `Your pond: ${site.pond_area_sqm.toLocaleString()} m², ${site.pond_volume_m3.toLocaleString()} m³`,
              { sticky: true },
            )
            .addTo(layer)
        }
      })
      L.circleMarker([site.location.latitude, site.location.longitude], {
        radius: 9, color: '#fff', weight: 2, fillColor: '#fbbf24', fillOpacity: 1,
      })
        .bindPopup(popupHtml('★', site, true))
        .bindTooltip('★ Your Site', {
          permanent: true,
          direction: 'right',
          offset: [10, 0],
          className: 'site-label',
        })
        .addTo(layer)
    }
  }, [analysis, customSite, selectedSite])

  // Fit bounds when results change
  useEffect(() => {
    const map = mapRef.current
    if (!map || !analysis) return
    const bounds = L.latLngBounds([])
    let has = false
    analysis.terrain.area_boundary.forEach((p) => {
      bounds.extend([p.latitude, p.longitude])
      has = true
    })
    if (has) map.fitBounds(bounds.pad(0.2))
  }, [analysis])

  return (
    <>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
      {drawTool === 'rectangle' && (
        <div className="draw-hint">⬜ Drag on the map to draw a rectangle</div>
      )}
      {drawTool === 'polygon' && (
        <div className="draw-hint" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span>⬠ Click to add corners ({polyVerts.length})</span>
          <button className="hint-btn" onClick={commitPolygon} disabled={polyVerts.length < 3}>
            Finish
          </button>
          <button className="hint-btn" onClick={clearPreview}>Cancel</button>
        </div>
      )}
    </>
  )
}
