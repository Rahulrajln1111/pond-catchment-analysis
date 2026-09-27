import { useState } from 'react'
import MapView, { type SelectionGeo, type DrawTool } from './MapView'
import type { AnalysisResponse, PondSite, SiteAnalysisResponse } from './types'

type Mode = 'draw' | 'kml'

/**
 * fetch with automatic retries for transient network failures.
 * The campus NAT drops a large share of new TCP connections, so a single
 * failed fetch must not surface as an error to the user.
 */
async function fetchRetry(
  input: string,
  init?: RequestInit,
  attempts = 4,
): Promise<Response> {
  let lastErr: unknown
  for (let i = 0; i < attempts; i++) {
    try {
      const resp = await fetch(input, init)
      // Retry only on server-side transient statuses
      if (resp.status >= 500 && i < attempts - 1) {
        lastErr = new Error(`HTTP ${resp.status}`)
      } else {
        return resp
      }
    } catch (e) {
      lastErr = e // NetworkError / timeout — retry
    }
    await new Promise((r) => setTimeout(r, 400 * (i + 1)))
  }
  throw lastErr instanceof Error ? lastErr : new Error('Network request failed')
}

export default function App() {
  const [mode, setMode] = useState<Mode>('draw')
  const [drawTool, setDrawTool] = useState<DrawTool>(null)
  const [selection, setSelection] = useState<SelectionGeo | null>(null)
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null)
  const [customSite, setCustomSite] = useState<PondSite | null>(null)
  const [selectedSite, setSelectedSite] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [depth, setDepth] = useState(2.0)
  const [contextId, setContextId] = useState<string | null>(null)

  const pickMode = (m: Mode) => {
    setMode(m)
    setDrawTool(null)
    setSelection(null)
    setAnalysis(null)
    setCustomSite(null)
    setSelectedSite(null)
    setError(null)
  }

  const analyze = async () => {
    if (!selection) return
    setBusy(true)
    setError(null)
    setCustomSite(null)
    setSelectedSite(null)
    setDrawTool(null) // exit drawing mode once an area has been analyzed
    try {
      const resp = await fetchRetry('/analyzeArea', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ polygon: selection, target_cell_m: 10, max_sites: 5 }),
      })
      const data = await resp.json()
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`)
      setAnalysis(data)
      setContextId(data.context_id ?? null)
    } catch (e: any) {
      setError(e.message || 'Analysis failed')
    } finally {
      setBusy(false)
    }
  }

  const analyzeKml = async (file: File) => {
    setBusy(true)
    setError(null)
    setCustomSite(null)
    setSelectedSite(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const resp = await fetchRetry('/analyzeContour', { method: 'POST', body: fd })
      const data = await resp.json()
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`)
      setAnalysis(data)
      setContextId(null) // KML flow has no drawn-area context
    } catch (e: any) {
      setError(e.message || 'KML analysis failed')
    } finally {
      setBusy(false)
    }
  }

  const pickPoint = async (lat: number, lng: number) => {
    if (!contextId || busy) return
    setBusy(true)
    setError(null)
    try {
      const resp = await fetchRetry('/analyzeSite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          context_id: contextId,
          latitude: lat,
          longitude: lng,
          pond_depth_m: depth,
        }),
      })
      const data: SiteAnalysisResponse = await resp.json()
      if (!resp.ok) throw new Error((data as any).detail || `HTTP ${resp.status}`)
      setCustomSite(data.site)
    } catch (e: any) {
      setError(e.message || 'Site analysis failed')
    } finally {
      setBusy(false)
    }
  }

  const exportKml = (i: number) => {
    if (!contextId) {
      setError('KML download is available for drawn-area analyses.')
      return
    }
    window.open(`/export/kml/${contextId}/${i}`, '_blank')
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>
          💧 Pond Catchment <span>Analyzer</span>
        </h1>
        <span className="badge">
          DEM: AWS Terrain Tiles · ~10 m grid · D8 flow routing
        </span>
      </header>

      <div className="main">
        <aside className="sidebar">
          <div className="btn-row">
            <button
              className={`btn ${mode === 'draw' ? 'primary' : ''}`}
              onClick={() => pickMode('draw')}
            >
              ✏️ Draw Area
            </button>
            <button
              className={`btn ${mode === 'kml' ? 'primary' : ''}`}
              onClick={() => pickMode('kml')}
            >
              📁 Upload KML
            </button>
          </div>

          {mode === 'draw' && (
            <div className="panel">
              <h2>1 · Select land area</h2>
              <p className="muted">
                Choose a draw tool, then select the region on the map:
              </p>
              <div className="btn-row" style={{ marginBottom: 8 }}>
                <button
                  className={`btn ${drawTool === 'rectangle' ? 'primary' : ''}`}
                  onClick={() => setDrawTool(drawTool === 'rectangle' ? null : 'rectangle')}
                >
                  ⬜ Rectangle
                </button>
                <button
                  className={`btn ${drawTool === 'polygon' ? 'primary' : ''}`}
                  onClick={() => setDrawTool(drawTool === 'polygon' ? null : 'polygon')}
                >
                  ⬠ Polygon
                </button>
              </div>
              <button
                className="btn primary full"
                onClick={analyze}
                disabled={!selection || busy}
              >
                {busy ? 'Analyzing…' : 'Analyze Selection'}
              </button>
              <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
                {selection ? (
                  <>
                    <span className="muted" style={{ fontSize: 12 }}>
                      ✅ {selection.coordinates[0].length - 1} corners
                    </span>
                    <button
                      className="btn"
                      style={{ padding: '4px 10px', fontSize: 12 }}
                      onClick={() => setSelection(null)}
                    >
                      🗑 Clear
                    </button>
                  </>
                ) : (
                  <span className="muted" style={{ fontSize: 12 }}>
                    Tip: after analyzing, click the map to test your own pond site.
                  </span>
                )}
              </div>
            </div>
          )}

          {mode === 'kml' && (
            <div className="panel">
              <h2>1 · Upload contour KML/KMZ</h2>
              <p className="muted">
                Upload a contour map (KML/KMZ, max 10 MB). Elevations come from the contour lines;
                rivers are auto-detected by color.
              </p>
              <input
                type="file"
                accept=".kml,.kmz"
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) analyzeKml(f)
                }}
              />
            </div>
          )}

          {error && <div className="error-box">⚠️ {error}</div>}
          {busy && (
            <div className="status-line">
              <div className="spinner" /> Running hydrology analysis…
            </div>
          )}

          {analysis && (
            <>
              <div className="panel">
                <h2>2 · Terrain summary</h2>
                <div className="stats">
                  <Stat
                    label="Elev range"
                    value={`${analysis.terrain.elevation_range_m.toFixed(0)} m`}
                  />
                  <Stat
                    label="Grid cell"
                    value={`${analysis.terrain.grid_resolution_m.toFixed(0)} m`}
                  />
                  <Stat label="Sites found" value={`${analysis.candidate_sites.length}`} />
                  <Stat
                    label="Analysis time"
                    value={`${(analysis.analysis_time_ms / 1000).toFixed(1)} s`}
                  />
                </div>
                <p className="muted" style={{ marginTop: 8 }}>
                  {analysis.message}
                </p>
              </div>

              <div className="panel">
                <h2>3 · Suggested pond sites</h2>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {analysis.candidate_sites.map((site, i) => (
                    <SiteCard
                      key={i}
                      i={i}
                      site={site}
                      selected={selectedSite === i}
                      onSelect={() => setSelectedSite(i)}
                      onExport={() => exportKml(i)}
                    />
                  ))}
                  {customSite && (
                    <SiteCard
                      i={-1}
                      site={customSite}
                      selected
                      onSelect={() => undefined}
                      onExport={() => undefined}
                    />
                  )}
                  {analysis.candidate_sites.length === 0 && !customSite && (
                    <p className="muted">
                      No viable pond sites in this selection — try a larger or hillier area.
                    </p>
                  )}
                </div>
              </div>

              {contextId && (
                <div className="panel">
                  <h2>4 · Test your own site</h2>
                  <p className="muted">
                    Click anywhere inside the analyzed area to compute the catchment and pond
                    volume for that exact point.
                  </p>
                  <div className="field">
                    <label>Pond depth: {depth.toFixed(1)} m</label>
                    <input
                      type="range"
                      min={0.5}
                      max={6}
                      step={0.5}
                      value={depth}
                      onChange={(e) => setDepth(parseFloat(e.target.value))}
                    />
                  </div>
                </div>
              )}
            </>
          )}
        </aside>

        <main className="map-wrap">
          <MapView
            selection={selection}
            onSelection={setSelection}
            drawTool={drawTool}
            analysis={analysis}
            customSite={customSite}
            selectedSite={selectedSite}
            onSelectSite={setSelectedSite}
            onMapClick={pickPoint}
            onToolDone={() => setDrawTool(null)}
          />
          {analysis && analysis.candidate_sites.length > 0 && (
            <div className="legend">
              <div className="row">
                <span className="swatch" style={{ background: 'rgba(14,165,233,.55)' }} />
                Pond water surface
              </div>
              <div className="row">
                <span
                  className="swatch"
                  style={{ background: 'transparent', border: '1px dashed #94a3b8' }}
                />
                Catchment area
              </div>
              {analysis.river_mask_boundary && analysis.river_mask_boundary.length > 0 && (
                <div className="row">
                  <span className="swatch river" />
                  River (excluded zone)
                </div>
              )}
            </div>
          )}
          {contextId && !busy && (
            <div className="draw-hint" style={{ top: 'auto', bottom: 18 }}>
              🖱️ Click the map to analyze a custom pond site
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  )
}

interface SiteCardProps {
  i: number
  site: PondSite
  selected: boolean
  onSelect: () => void
  onExport: () => void
}

function SiteCard({ i, site, selected, onSelect, onExport }: SiteCardProps) {
  const isCustom = i === -1
  return (
    <div
      className={`site-card ${selected ? 'selected' : ''}`}
      onClick={onSelect}
      style={isCustom ? { borderLeftColor: '#fbbf24' } : undefined}
    >
      <div className="head">
        <span className="rank">
          {isCustom ? '★ Your clicked site' : `Site ${i + 1}`}
        </span>
        <span className="vol">{Math.round(site.pond_volume_m3).toLocaleString()} m³</span>
      </div>
      <div className="meta">
        <span>💧 {Math.round(site.pond_area_sqm).toLocaleString()} m²</span>
        <span>⛰️ {site.catchment_area_hectares.toFixed(2)} ha</span>
        <span>📏 elev {site.elevation_m.toFixed(1)} m</span>
      </div>
      {!isCustom && (
        <div style={{ marginTop: 8 }}>
          <button
            className="btn"
            style={{ padding: '5px 10px', fontSize: 12 }}
            onClick={(e) => {
              e.stopPropagation()
              onExport()
            }}
          >
            ⬇ Download KML
          </button>
        </div>
      )}
    </div>
  )
}
