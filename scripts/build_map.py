# scripts/build_map.py
# ACA Americas map with:
#  - level badge stacked ABOVE IATA in each label
#  - optional highlighting (bigger dot + yellow stroke) for a set of IATA codes
#  - non-interest airports: no border, no labels, dimmed & smaller
# build_map(highlight_iatas=None) -> folium.Map
# If executed as a script, writes docs/aca_map.html.

import io
import os
import sys
from datetime import datetime, timezone

import folium
import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---------- config ----------
LEVELS = ['Level 1', 'Level 2', 'Level 3', 'Level 3+', 'Level 4', 'Level 4+', 'Level 5']

PALETTE = {
    "Level 1": "#5B2C6F",
    "Level 2": "#00AEEF",
    "Level 3": "#1F77B4",
    "Level 3+": "#2ECC71",
    "Level 4": "#F4D03F",
    "Level 4+": "#E39A33",
    "Level 5": "#E74C3C",
}

# Show as a short badge above IATA
LEVEL_BADGE = {
    "Level 1": "1",
    "Level 2": "2",
    "Level 3": "3",
    "Level 3+": "3+",
    "Level 4": "4",
    "Level 4+": "4+",
    "Level 5": "5",
}

RADIUS = {"large": 8, "medium": 7, "small": 6}
STROKE = 2

LABEL_GAP_PX = 10  # vertical gap between dot and label

# --- Zoom tuning knobs (triple speed) ---
ZOOM_SNAP = 0.10
ZOOM_DELTA = 0.75          # was 0.25 → triple zoom per tick
WHEEL_PX_PER_ZOOM = 100    # was 300 → triple scroll sensitivity
WHEEL_DEBOUNCE_MS = 10

# --- Position DB knobs ---
DB_MAX_HISTORY = 200
UPDATE_DEBOUNCE_MS = 120

# --- Stacking behavior ---
STACK_ON_AT_Z = 7.5
HIDE_LABELS_BELOW_Z = 4.4  # labels visible at >= 4.4

# --- Grouping distance in miles ---
GROUP_RADIUS_MILES = 30.0

# --- Visual tweak for stacked rows ---
STACK_ROW_GAP_PX = 6

# default standalone output
OUT_DIR = "docs"
OUT_FILE = os.path.join(OUT_DIR, "aca_map.html")


# ---------- helpers ----------
def write_error_page(msg: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = """<!doctype html><meta charset="utf-8">
<title>ACA Americas map</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<meta http-equiv="Expires" content="0"/>
<style>body{font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;padding:24px;color:#233;max-width:900px;margin:auto;background:#f6f8fb}
.card{background:#fff;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.08);padding:20px}
h1{margin:0 0 10px 0}code{background:#f5f7fb;padding:2px 6px;border-radius:6px}</style>
<div class="card">
  <h1>ACA Americas map</h1>
  <p><strong>Status:</strong> temporarily unavailable.</p>
  <p><strong>Reason:</strong> __MSG__</p>
  <p>Last attempt: __UPDATED__. This page updates when the generator runs.</p>
</div>""".replace("__MSG__", msg).replace("__UPDATED__", updated)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print("Wrote fallback page:", OUT_FILE)


def fetch_aca_html(timeout: int = 45) -> str:
    url = "https://www.airportcarbonaccreditation.org/accredited-airports/"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ACA-Map-Bot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_aca_table(html: str) -> pd.DataFrame:
    """Return dataframe with: iata, airport, country, region, aca_level, region4."""
    soup = BeautifulSoup(html, "lxml")
    dfs = []

    table = soup.select_one(".airports-listview table")
    if table is not None:
        try:
            dfs = pd.read_html(io.StringIO(str(table)))
        except Exception:
            pass

    if not dfs:
        all_tables = pd.read_html(html)
        target = None
        want = {"airport", "airport code", "country", "region", "level"}
        for df in all_tables:
            cols = {str(c).strip().lower() for c in df.columns}
            if want.issubset(cols):
                target = df
                break
        if target is None:
            raise RuntimeError("ACA table not found on the page.")
        dfs = [target]

    raw = dfs[0]
    aca = (
        raw.rename(
            columns={
                "Airport": "airport",
                "Airport code": "iata",
                "Country": "country",
                "Region": "region",
                "Level": "aca_level",
            }
        )[["iata", "airport", "country", "region", "aca_level"]]
    )

    def region4(r: str) -> str:
        if r in ("North America", "Latin America & the Caribbean"):
            return "Americas"
        if r == "UKIMEA":
            return "Europe"
        return r

    aca["region4"] = aca["region"].map(region4)
    aca = aca[aca["aca_level"].isin(LEVELS)].dropna(subset=["iata"]).copy()
    aca["iata"] = aca["iata"].astype(str).str.upper()
    return aca


def load_coords() -> pd.DataFrame:
    url = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
    use = ["iata_code", "latitude_deg", "longitude_deg", "type", "name", "iso_country"]
    df = pd.read_csv(url, usecols=use).rename(columns={"iata_code": "iata"})
    df = df.dropna(subset=["iata", "latitude_deg", "longitude_deg"]).copy()
    df["iata"] = df["iata"].astype(str).str.upper()
    df["size"] = df["type"].map({"large_airport": "large", "medium_airport": "medium"}).fillna("small")
    return df


# ---------- main ----------
def build_map(highlight_iatas=None) -> folium.Map:
    """
    Return a folium.Map for ACA airports in the Americas.
    highlight_iatas: optional set/list of IATA codes to emphasize (bigger + yellow stroke + labels).
    """
    # NEW: preserve order (first = chosen), and a set for membership tests
    highlight_list = list(highlight_iatas or [])
    highlight = {str(x).upper() for x in highlight_list}
    chosen = highlight_list[0].upper() if highlight_list else None

    aca_html = fetch_aca_html()
    aca = parse_aca_table(aca_html)
    coords = load_coords()

    amer = (
        aca[aca["region4"].eq("Americas")]
        .merge(coords, on="iata", how="left")
        .dropna(subset=["latitude_deg", "longitude_deg"])
    )
    if amer.empty:
        raise RuntimeError("No rows for the Americas after joining coordinates.")

    # Center: centroid of all plotted points; zoom: open at 4.7
    center_lat = float(amer["latitude_deg"].mean())
    center_lon = float(amer["longitude_deg"].mean())

    m = folium.Map(
        tiles="CartoDB Positron",
        zoomControl=True,
        prefer_canvas=True,
        location=[center_lat, center_lon],
        zoom_start=4.7,
    )

    groups = {lvl: folium.FeatureGroup(name=lvl, show=True).add_to(m) for lvl in LEVELS}

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    BUILD_VER = "r1.9-zoomx3+open4.7+yellow-hot+nolabels-cold+no-border-cold+labels-comma-level+exportjpeg"

    # --- CSS + footer badge + zoom meter + stack styles ---
    badge_html = (
        r"""
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<meta http-equiv="Expires" content="0"/>
<style>
.leaflet-tooltip.iata-tt{
  background: transparent; border: 0; box-shadow: none;
  color: #2e2e2e;
  font-family: "Open Sans","Helvetica Neue",Arial,sans-serif;
  font-weight: 900; font-size: 12px; letter-spacing: 0.5px;
  text-transform: uppercase; white-space: nowrap; text-align:center;
}
.leaflet-tooltip-top:before,
.leaflet-tooltip-bottom:before,
.leaflet-tooltip-left:before,
.leaflet-tooltip-right:before{ display:none !important; }

/* We feed the tooltip HTML already containing .ttxt */
.leaflet-tooltip.iata-tt .ttxt { display:inline-block; line-height:1.05; }
/* keep legacy classes harmless */
.leaflet-tooltip.iata-tt .lvlchip { display:none; }
.leaflet-tooltip.iata-tt .iata    { display:none; }

.leaflet-control-layers-expanded{ box-shadow:0 4px 14px rgba(0,0,0,.12); border-radius:10px; }
.last-updated {
  position:absolute; right:12px; bottom:12px; z-index:9999;
  background:#fff; padding:6px 8px; border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif; color:#485260;
}
.zoom-meter{
  position:absolute; left:12px; top:112px; z-index:9999;
  background:#fff; padding:6px 8px; border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif; color:#485260;
  user-select:none; pointer-events:none;
}

/* Stack list styled like labels (no bg/border/shadow) */
/* CHANGED: color to black (no grey when stacked) */
.iata-stack{
  position:absolute; z-index:9998; pointer-events:none;
  background:transparent; border:0; box-shadow:none;
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif;
  color:#000; letter-spacing:0.5px; text-transform:uppercase;
  font-weight:1000; text-align:left; white-space:nowrap;
}
.iata-stack .row{ line-height:1.0; margin: __ROWGAP__px 0; }

/* Legend box under zoom meter (dots already, keep) */
.legend-box{
  position:absolute; left:12px; top:170px; z-index:9999;
  background:#fff; padding:6px 8px; border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif; color:#485260;
  user-select:none;
}
.legend-box .title{ font-weight:600; margin-bottom:4px; }
.legend-box .row{ display:flex; align-items:center; gap:6px; margin:3px 0; }
.legend-box .dot{ width:10px; height:10px; border-radius:50%; display:inline-block; border:1px solid rgba(0,0,0,.25); }

/* NEW: download button */
#downloadBtn{
  position:absolute; top:12px; left:12px; z-index:9999;
  background:#3498db; color:#fff; border:none; border-radius:6px;
  padding:6px 12px; cursor:pointer;
}
</style>
<div class="last-updated">Last updated: __UPDATED__ • __VER__</div>
<div id="zoomMeter" class="zoom-meter">Zoom: --%</div>
<button id="downloadBtn" type="button">Download as JPEG</button>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
  // High-res JPEG export of current map view; hides UI elements temporarily.
  (function(){
    function el(q){ return document.querySelector(q); }
    function doExport(){
      const hideElems = [
        el('.leaflet-control-layers'),
        el('.leaflet-control-zoom'),
        el('.last-updated'),
        el('#zoomMeter')
      ];
      hideElems.forEach(e=>{ if(e) e.dataset._prevDisplay = e.style.display; if(e) e.style.display='none'; });
      html2canvas(document.querySelector('.leaflet-container'), { scale: 3 }).then(canvas=>{
        const a = document.createElement('a');
        a.download = 'aca_map.jpeg';
        a.href = canvas.toDataURL('image/jpeg', 1.0);
        a.click();
        hideElems.forEach(e=>{ if(e) e.style.display = (e && e.dataset._prevDisplay) || ''; });
      });
    }
    var btn = document.getElementById('downloadBtn');
    if (btn) btn.addEventListener('click', doExport);
  })();
</script>
"""
        .replace("__UPDATED__", updated)
        .replace("__VER__", BUILD_VER)
        .replace("__ROWGAP__", str(int(STACK_ROW_GAP_PX)))
    )
    m.get_root().html.add_child(folium.Element(badge_html))

    # --- legend (under zoom meter) ---
    legend_items = "".join(
        '<div class="row"><span class="dot" style="background:{color}"></span>{lvl}</div>'.format(
            color=PALETTE.get(lvl, "#666"), lvl=lvl
        )
        for lvl in reversed(LEVELS)
    )
    legend_html = (
        '<div class="legend-box">'
        '<div class="title">ACA Level</div>'
        f'{legend_items}'
        '</div>'
    )
    m.get_root().html.add_child(folium.Element(legend_html))

    # --- dots + permanent tooltips (labels) ---
    for _, r in amer.iterrows():
        lat, lon = float(r.latitude_deg), float(r.longitude_deg)
        size_key = r.get("size", "small")
        base_radius = RADIUS.get(size_key, 6)

        # Determine highlight type
        if chosen and r.iata == chosen:
            # CHANGED: chosen airport gets RED outline
            radius = base_radius * 1.5
            stroke_color = "#E74C3C"     # red outline
            stroke_weight = max(STROKE, 3)
            fill_opacity = 0.95
            add_label = True
        elif r.iata in highlight:
            # Other competitors: YELLOW outline (as table)
            radius = base_radius * 1.5
            stroke_color = "#F1C40F"     # yellow outline
            stroke_weight = max(STROKE, 3)
            fill_opacity = 0.95
            add_label = True
        else:
            # Non-interest ports: NO border, smaller, dimmer, and NO LABEL
            radius = base_radius * 0.75
            stroke_color = "transparent"
            stroke_weight = 0
            fill_opacity = 0.50
            add_label = False

        offset_y = -(radius + max(stroke_weight, 0) + max(LABEL_GAP_PX, 1))

        dot = folium.CircleMarker(
            [lat, lon],
            radius=float(radius),
            color=stroke_color,
            weight=int(stroke_weight),
            fill=True,
            fill_color=PALETTE.get(r.aca_level, "#666"),
            fill_opacity=float(fill_opacity),
            popup=folium.Popup(
                "<b>{airport}</b><br>IATA: {iata}<br>ACA: <b>{lvl}</b><br>Country: {ctry}".format(
                    airport=r.airport, iata=r.iata, lvl=r.aca_level, ctry=r.country
                ),
                max_width=320,
            ),
        )

        # CHANGED: label content -> "IATA, LEVELBADGE" (single line), for chosen + competitors
        if add_label:
            lvl_badge = LEVEL_BADGE.get(r.aca_level, "")
            label_html = f'<div class="ttxt">{r.iata}, {lvl_badge}</div>'
            dot.add_child(
                folium.Tooltip(
                    label_html,
                    permanent=True,
                    direction="top",
                    offset=(0, offset_y),
                    sticky=False,
                    class_name=f"iata-tt size-{size_key} tt-{r.iata}",
                    parse_html=True,
                )
            )

        dot.add_to(groups[r.aca_level])

    folium.LayerControl(collapsed=False).add_to(m)

    # --- JS: smooth zoom + zoom meter + position DB + stacks on zoom-out + miles->px scaling ---
    js = r"""
(function(){
  try {
    const MAP_NAME = "__MAP_NAME__";
    const ZOOM_SNAP = __ZOOM_SNAP__;
    const ZOOM_DELTA = __ZOOM_DELTA__;
    const WHEEL_PX = __WHEEL_PX__;
    const WHEEL_DEBOUNCE = __WHEEL_DEBOUNCE__;
    const DB_MAX_HISTORY = __DB_MAX_HISTORY__;
    const UPDATE_DEBOUNCE_MS = __UPDATE_DEBOUNCE_MS__;

    const STACK_ON_AT_Z = __STACK_ON_AT_Z__;
    const HIDE_LABELS_BELOW_Z = __HIDE_LABELS_BELOW_Z__;
    const GROUP_RADIUS_MILES = __GROUP_RADIUS_MILES__;

    window.ACA_DB = window.ACA_DB || { latest:null, history:[] };

    function until(cond, cb, tries=200, delay=50){
      (function tick(n){ if(cond()) return cb(); if(n<=0) return; setTimeout(()=>tick(n-1), delay); })(tries);
    }

    until(()=> typeof window[MAP_NAME] !== "undefined" &&
             window[MAP_NAME] &&
             window[MAP_NAME].getPanes &&
             window[MAP_NAME].getContainer,
          init, 200, 50);

    function init(){
      const map  = window[MAP_NAME];
      const pane = map.getPanes().tooltipPane;

      function tuneWheel(){
        map.options.zoomSnap = ZOOM_SNAP;
        map.options.zoomDelta = ZOOM_DELTA;
        map.options.wheelPxPerZoomLevel = WHEEL_PX;
        map.options.wheelDebounceTime   = WHEEL_DEBOUNCE;
        if (map.scrollWheelZoom){ map.scrollWheelZoom.disable(); map.scrollWheelZoom.enable(); }
      }
      tuneWheel();

      const meter = document.getElementById('zoomMeter');
      function updateMeter(){
        if (!meter) return;
        const z = map.getZoom();
        const minZ = (map.getMinZoom && map.getMinZoom()) || 0;
        let maxZ = (map.getMaxZoom && map.getMaxZoom()); if (maxZ == null) maxZ = 19;
        const pct = Math.round(((z - minZ)/Math.max(1e-6, (maxZ - minZ))) * 100);
        meter.textContent = "Zoom: " + pct + "% (z=" + z.toFixed(2) + ")";
      }

      function rectBaseForPane(thePane){
        const prect = thePane.getBoundingClientRect();
        return function rect(el){
          const r = el.getBoundingClientRect();
          return { x:r.left - prect.left, y:r.top - prect.top, w:r.width, h:r.height };
        };
      }
      function clearStacks(){ pane.querySelectorAll('.iata-stack').forEach(n => n.remove()); }
      function showAllLabels(){ pane.querySelectorAll('.iata-tt').forEach(el => { el.style.display = ''; }); }
      function hideAllLabels(){ pane.querySelectorAll('.iata-tt').forEach(el => { el.style.display = 'none'; }); }

      function milesToPixels(miles){
        const meters = miles * 1609.344;
        const center = map.getCenter();
        const p1 = map.latLngToContainerPoint(center);
        const p2 = L.point(p1.x + 100, p1.y);
        const ll2 = map.containerPointToLatLng(p2);
        const metersPer100px = map.distance(center, ll2);
        const pxPerMeter = 100 / Math.max(1e-6, metersPer100px);
        return meters * pxPerMeter;
      }

      function collectItems(){
        const rect = rectBaseForPane(pane);
        const items = [];
        map.eachLayer(lyr=>{
          if (!(lyr instanceof L.CircleMarker)) return;
          const tt = (lyr.getTooltip && lyr.getTooltip()) || null;
          if (!tt || !tt._container) return;
          const el = tt._container;
          if (!el || !el.classList.contains('iata-tt')) return;
          el.style.display = '';
          const latlng = lyr.getLatLng();
          const pt = map.latLngToContainerPoint(latlng);
          const cls = Array.from(el.classList);
          const size = (cls.find(c=>c.startsWith('size-'))||'size-small').slice(5);
          const iata = (cls.find(c=>c.startsWith('tt-'))||'tt-').slice(3);

          // CHANGED: also capture level from text "IATA, LEVEL"
          const txt = (el.textContent || '').split(',');
          const level = (txt.length > 1 ? txt[1].trim() : '');

          const R = rect(el.querySelector('.ttxt') || el);
          items.push({ iata, level, size, el,
            dot:{ lat:latlng.lat, lng:latlng.lng, x:pt.x, y:pt.y },
            label:{ x:R.x, y:R.y, w:R.w, h:R.h } });
        });
        return items;
      }

      function buildClusters(items, radiusPx){
        const n = items.length;
        const parent = Array.from({length:n}, (_,i)=>i);
        function find(a){ return parent[a]===a ? a : (parent[a]=find(parent[a])); }
        function uni(a,b){ a=find(a); b=find(b); if(a!==b) parent[b]=a; }
        const R2 = radiusPx * radiusPx;
        for (let i=0;i<n;i++){
          for (let j=i+1;j<n;j++){
            const dx = items[i].dot.x - items[j].dot.x;
            const dy = items[i].dot.y - items[j].dot.y;
            if (dx*dx + dy*dy <= R2) uni(i,j);
          }
        }
        const groups = new Map();
        for (let i=0;i<n;i++){
          const r = find(i);
          if (!groups.has(r)) groups.set(r, []);
          groups.get(r).push(i);
        }
        return Array.from(groups.values()).filter(g => g.length >= 2);
      }

      function drawStack(groupIdxs, items){
        const div = document.createElement('div');
        div.className = 'iata-stack';
        const sorted = groupIdxs.slice().sort((a,b)=> items[a].label.y - items[b].label.y);
        const anchorIdx = sorted[0];
        const anchor = items[anchorIdx];
        sorted.forEach(i=>{
          const r = document.createElement('div');
          r.className = 'row';
          // CHANGED: include level "IATA, LEVEL"
          r.textContent = items[i].iata + (items[i].level ? (", " + items[i].level) : "");
          div.appendChild(r);
        });
        const pane = map.getPanes().tooltipPane;
        pane.appendChild(div);
        requestAnimationFrame(()=>{
          const stackRect = div.getBoundingClientRect();
          const extraH = Math.max(0, stackRect.height - anchor.label.h);
          const left = Math.round(anchor.label.x);
          const top  = Math.round(anchor.label.y - extraH);
          div.style.left = left + "px";
          div.style.top  = top  + "px";
        });
        return { iatas: sorted.map(i=>items[i].iata) };
      }

      function applyClustering(items){
        clearStacks();
        showAllLabels();
        const z = map.getZoom();
        if (z < HIDE_LABELS_BELOW_Z){ hideAllLabels(); return; }
        if (z > STACK_ON_AT_Z) return;
        const radiusPx = milesToPixels(GROUP_RADIUS_MILES);
        const clusters = buildClusters(items, radiusPx);
        clusters.forEach(g=>{
          g.forEach(i=>{ items[i].el.style.display = 'none'; });
          drawStack(g, items);
        });
      }

      function updateAll(){
        updateMeter();
        requestAnimationFrame(()=>requestAnimationFrame(()=>{
          const items = collectItems();
          applyClustering(items);
        }));
      }
      function updateMeter(){
        if (!meter) return;
        const z = map.getZoom();
        const minZ = (map.getMinZoom && map.getMinZoom()) || 0;
        let maxZ = (map.getMaxZoom && map.getMaxZoom()); if (maxZ == null) maxZ = 19;
        const pct = Math.round(((z - minZ)/Math.max(1e-6, (maxZ - minZ))) * 100);
        meter.textContent = "Zoom: " + pct + "% (z=" + z.toFixed(2) + ")";
      }
      function scheduleUpdate(){ setTimeout(updateAll, __UPDATE_DEBOUNCE_MS__); }

      if (map.whenReady) map.whenReady(updateAll);
      map.on('zoomend moveend overlayadd overlayremove layeradd layerremove resize', scheduleUpdate);
      updateAll();
    }
  } catch (err) {
    console.error("[ACA] init failed:", err);
  }
})();
"""

    js = (js
          .replace("__MAP_NAME__", m.get_name())
          .replace("__ZOOM_SNAP__", str(float(ZOOM_SNAP)))
          .replace("__ZOOM_DELTA__", str(float(ZOOM_DELTA)))
          .replace("__WHEEL_PX__", str(int(WHEEL_PX_PER_ZOOM)))
          .replace("__WHEEL_DEBOUNCE__", str(int(WHEEL_DEBOUNCE_MS)))
          .replace("__DB_MAX_HISTORY__", str(int(DB_MAX_HISTORY)))
          .replace("__UPDATE_DEBOUNCE_MS__", str(int(UPDATE_DEBOUNCE_MS)))
          .replace("__STACK_ON_AT_Z__", str(float(STACK_ON_AT_Z)))
          .replace("__HIDE_LABELS_BELOW_Z__", str(float(HIDE_LABELS_BELOW_Z)))
          .replace("__GROUP_RADIUS_MILES__", str(float(GROUP_RADIUS_MILES)))
    )

    m.get_root().script.add_child(folium.Element(js))
    return m


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        fmap = build_map()
        fmap.save(OUT_FILE)
        print("Wrote", OUT_FILE)
    except Exception as e:
        print("ERROR building map:", e, file=sys.stderr)
        write_error_page(str(e))
        sys.exit(0)
