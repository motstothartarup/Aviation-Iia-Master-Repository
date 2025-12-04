# scripts/build_map.py
# ACA Americas map with:
#  - target airport highlighted (red outline), others plain fills
#  - optional highlighting for a set of IATA codes
#  - labels "IATA, LEVEL" or "IATA, Unscored" for unknowns
#  - custom legend and zoom meter, no extra Leaflet legend or export button

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
ZOOM_DELTA = 0.75
WHEEL_PX_PER_ZOOM = 100
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

# read target + composite list from the grid output
GRID_DEFAULT_PATH = os.path.join("docs", "grid.html")


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


def _parse_grid_composite7(grid_html_path: str = GRID_DEFAULT_PATH):
    """
    Return (target_iata, composite_list_of_7) by reading docs/grid.html.
    Target parsed from header "<h3>JFK — ...</h3>", composite from the row whose .cat contains 'composite'.
    """
    try:
        if not os.path.exists(grid_html_path):
            return None, []
        with open(grid_html_path, "r", encoding="utf-8") as f:
            html = f.read()
        soup = BeautifulSoup(html, "lxml")

        target = None
        h = soup.select_one(".header h3")
        if h:
            txt = (h.get_text() or "").strip()
            target = (txt.split("—", 1)[0] or "").strip().upper()

        comp_row = None
        for row in soup.select(".container .row"):
            cat = row.select_one(".cat")
            if not cat:
                continue
            label = " ".join((cat.get_text() or "").strip().lower().split())
            if "composite" in label:
                comp_row = row
                break
        if not comp_row:
            return target, []

        chips = comp_row.select(".grid .chip")
        out = []
        for ch in chips:
            if "origin" in (ch.get("class", []) or []):
                continue
            code_el = ch.select_one(".code")
            if not code_el:
                continue
            code = code_el.get_text(strip=True).upper()
            if code and len(code) <= 4:
                out.append(code)
            if len(out) >= 7:
                break
        return target, out
    except Exception:
        return None, []


# ---------- main ----------
def build_map(highlight_iatas=None) -> folium.Map:
    """
    Return a folium.Map for ACA airports in the Americas.

    highlight_iatas: optional set/list of IATA codes to emphasize.
      - Target airport is taken from the grid HTML and gets a red outline.
      - Other airports get filled circles with no visible outline.
    If highlight_iatas is None, we read [target + composite-7] from docs/grid.html.
    """
    parsed_target, parsed_comp = _parse_grid_composite7(GRID_DEFAULT_PATH)

    if not highlight_iatas:
        if parsed_target and parsed_comp:
            highlight_iatas = [parsed_target] + parsed_comp
        else:
            print("[WARN] Could not parse Composite 7 from grid; proceeding without highlight set.", file=sys.stderr)

    highlight_list = [str(x).upper() for x in (highlight_iatas or [])]
    # Ensure the chosen airport is the grid target when possible
    if parsed_target and parsed_target.upper() in highlight_list:
        chosen = parsed_target.upper()
    elif highlight_list:
        chosen = highlight_list[0]   # fall back to the first code you passed in
    else:
        chosen = None


    highlight = set(highlight_list)

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

    # Guarantee all requested highlight codes can render, even if not ACA-scored
    if highlight:
        present_set = set(amer["iata"])
        missing = [c for c in highlight_list if c not in present_set]
        if missing:
            extra = coords[coords["iata"].isin(missing)].copy()
            if not extra.empty:
                extra = extra.assign(
                    airport=extra.get("name", extra["iata"]),
                    country=extra.get("iso_country", ""),
                    region="",
                    aca_level="Unknown",
                    region4="Americas",
                )
                keep_cols = list(amer.columns)
                for col in keep_cols:
                    if col not in extra.columns:
                        extra[col] = None
                amer = pd.concat([amer, extra[keep_cols]], ignore_index=True, sort=False)
                amer = amer.drop_duplicates(subset=["iata"], keep="first")

    # Plot only the highlight set if present
    plot_df = amer[amer["iata"].isin(highlight)].copy() if highlight else amer.copy()
    if highlight:
        order_map = {code: i for i, code in enumerate(highlight_list)}
        plot_df["__order__"] = plot_df["iata"].map(order_map).fillna(9999).astype(int)
        plot_df = plot_df.sort_values("__order__")

    center_lat = float(plot_df["latitude_deg"].mean())
    center_lon = float(plot_df["longitude_deg"].mean())

    m = folium.Map(
        tiles="CartoDB Positron",
        zoomControl=True,
        prefer_canvas=True,
        location=[center_lat, center_lon],
        zoom_start=4.7,
    )

    groups = {lvl: folium.FeatureGroup(name=lvl, show=True).add_to(m) for lvl in (LEVELS + ["Unknown"])}

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # footer + zoom meter + stack styles
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

.leaflet-tooltip.iata-tt .ttxt { display:inline-block; line-height:1.05; }

/* legacy spans suppressed (we now show "IATA, LEVEL" text only) */
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
  position:absolute; left:12px; top:112px; z-indexf:9999;
  background:#fff; padding:6px 8px; border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif; color:#485260;
  user-select:none; pointer-events:none;
}

/* stacked label list */
.iata-stack{
  position:absolute; z-index:9998; pointer-events:none;
  background:transparent; border:0; box-shadow:none;
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif;
  color:#000; letter-spacing:0.5px; text-transform:uppercase;
  font-weight:1000; text-align:left; white-space:nowrap;
}
.iata-stack .row{ line-height:1.0; margin: __ROWGAP__px 0; }

/* Legend box */
.legend-box{
  position:absolute; left:12px; top:170px; z-index:9999;
  background:#fff; padding:6px 8px; border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif; color:#485260;
  user-select:none;
}
.legend-box .title{ font-weight:600; margin-bottom:4px; }
.legend-box .row{ display:flex; align-items:center; gap:6px; margin:3px 0; }
.legend-box .dot{
  width:10px; height:10px;
  border-radius:50%;
  display:inline-block;
  border:1px solid rgba(0,0,0,.25);
}
</style>
<div class="last-updated">Last updated: __UPDATED__</div>
<div id="zoomMeter" class="zoom-meter">Zoom: --%</div>
"""
        .replace("__UPDATED__", updated)
        .replace("__ROWGAP__", str(int(STACK_ROW_GAP_PX)))
    )
    m.get_root().html.add_child(folium.Element(badge_html))

    # custom legend (simple circles)
    legend_items = "".join(
        '<div class="row"><span class="dot" style="background:{color}"></span>{lvl}</div>'.format(
            color=PALETTE.get(lvl, "#666"), lvl=lvl
        )
        for lvl in reversed(LEVELS + ["Unknown"])
    )
    legend_html = (
        '<div class="legend-box">'
        '<div class="title">ACA Level</div>'
        f'{legend_items}'
        '</div>'
    )
    m.get_root().html.add_child(folium.Element(legend_html))

    # dots + permanent tooltips
    for _, r in plot_df.iterrows():
        lat, lon = float(r.latitude_deg), float(r.longitude_deg)
        size_key = r.get("size", "small")
        base_radius = RADIUS.get(size_key, 6)

        # All airports styled the same, no highlighted circle
        radius = base_radius * 1.5
        stroke_color = "rgba(0,0,0,0)"
        stroke_weight = 0

        fill_opacity = 0.95
        add_label = True
        offset_y = -(radius + max(stroke_weight, 0) + max(LABEL_GAP_PX, 1))

        lvl = r.aca_level if r.aca_level in PALETTE else "Unknown"
        dot = folium.CircleMarker(
            [lat, lon],
            radius=float(radius),
            color=stroke_color,
            weight=int(stroke_weight),
            fill=True,
            fill_color=PALETTE.get(lvl, "#666"),
            fill_opacity=float(fill_opacity),
            popup=folium.Popup(
                "<b>{airport}</b><br>IATA: {iata}<br>ACA: <b>{lvl}</b><br>Country: {ctry}".format(
                    airport=r.airport if pd.notna(r.get("airport")) and str(r.get("airport")).strip() else r.iata,
                    iata=r.iata, lvl=lvl, ctry=r.get("country", "")
                ),
                max_width=320,
            ),
        )

        if add_label:
            if lvl == "Unknown":
                label_text = f"{r.iata}, Unscored"
            else:
                lvl_badge = LEVEL_BADGE.get(lvl, "")
                label_text = f"{r.iata}, {lvl_badge}"

      
             # Highlight the single chosen airport's label text in red
            label_color_style = ' style="color:#E74C3C;"' if (chosen and r.iata == chosen) else ""
            label_html = f'<div class="ttxt"{label_color_style}>{label_text}</div>'


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


        dot.add_to(groups[lvl])


    # Remove Leaflet LayerControl to avoid duplicate legend / "cartodbpositron" entry
    # (we keep only the custom legend box above)
    # folium.LayerControl(collapsed=False).add_to(m)

    # JS: zoom meter + clustering (unchanged except export removed)
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

          const txt = (el.textContent || '').split(',');
          const level = (txt.length > 1 ? txt[1].trim() : '');

          const R0 = el.querySelector('.ttxt') || el;
          const prect = rect(R0);
          items.push({ iata, level, size, el,
            dot:{ lat:latlng.lat, lng:latlng.lng, x:pt.x, y:pt.y },
            label:{ x:prect.x, y:prect.y, w:prect.w, h:prect.h } });
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
