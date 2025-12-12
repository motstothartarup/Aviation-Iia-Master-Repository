# scripts/build_map.py
# ACA map with:
#  - target airport highlighted (red label), others plain fills
#  - optional highlighting for a set of IATA codes
#  - labels "IATA, LEVEL" or "IATA, N/A" for unknowns
#  - custom legend and zoom meter, stacked labels, and
#  - dynamic add/remove of markers in response to ACA table clicks.
#
# Fixes:
#  - Do NOT restrict ACA data to Americas Only (international peers get correct ACA colors)
#  - Initial view opens to target's region group (from docs/grid.html)

import io
import os
import sys
import json
import re
from datetime import datetime, timezone

import folium
import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---------- config ----------
LEVELS = ['Level 1', 'Level 2', 'Level 3', 'Level 3+',
          'Level 4', 'Level 4+', 'Level 5']

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

# vertical gap between dot and label (base) + scale factor to pull labels closer
LABEL_GAP_PX = 5
LABEL_OFFSET_SCALE = 0.7  # tweak between ~0.5 and 1.0 to taste

# --- Zoom tuning knobs ---
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
STACK_ROW_GAP_PX = 10

# default standalone output
OUT_DIR = "docs"
OUT_FILE = os.path.join(OUT_DIR, "aca_map.html")

# read target + composite list from the grid output
GRID_DEFAULT_PATH = os.path.join("docs", "grid.html")

# Region-group view presets (for initial map view)
# These are intentionally broad, so they "feel right" for each group.
REGION_GROUP_BOUNDS = {
    "Americas": [[15, -130], [55, -60]],          # USA-focused view
    "Europe": [[20, -20], [62, 45]],              # Europe + North Africa
    "UKIMEA": [[5, -20], [60, 95]],               # UK/Europe through Middle East + India
    "Asia Pacific": [[-45, 90], [55, 180]],       # APAC + Australia
}


# ---------- helpers ----------
def write_error_page(msg: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = """<!doctype html><meta charset="utf-8">
<title>ACA map</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<meta http-equiv="Expires" content="0"/>
<style>body{font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;padding:24px;color:#233;max-width:900px;margin:auto;background:#f6f8fb}
.card{background:#fff;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.08);padding:20px}
h1{margin:0 0 10px 0}code{background:#f5f7fb;padding:2px 6px;border-radius:6px}</style>
<div class="card">
  <h1>ACA map</h1>
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
            dfs = []

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

    # Keep the original ACA regions, but also compute region4 for the table payload compatibility.
    def region4(r: str) -> str:
        if r in ("North America", "Latin America & the Caribbean"):
            return "Americas"
        if r == "UKIMEA":
            return "UKIMEA"
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
    df["size"] = df["type"].map(
        {"large_airport": "large", "medium_airport": "medium"}
    ).fillna("small")
    return df


def _parse_grid_target_and_region_group(grid_html_path: str = GRID_DEFAULT_PATH):
    """
    Reads docs/grid.html and tries to extract:
      - target IATA (from the first header h3: "<IATA> - overview ...")
      - region group (from the section header: "... regional peers ... (REGION)")
    Returns: (target_iata_or_none, region_group_or_none)
    """
    try:
        if not os.path.exists(grid_html_path):
            return None, None
        with open(grid_html_path, "r", encoding="utf-8") as f:
            html = f.read()
        soup = BeautifulSoup(html, "lxml")

        # Target IATA
        target = None
        h = soup.select_one(".header h3")
        if h:
            txt = (h.get_text() or "").strip()
            # expected: "LAX - overview of airports with similar throughput."
            m = re.match(r"^\s*([A-Z0-9]{2,4})\s*[-–—]", txt.upper())
            if m:
                target = m.group(1).strip().upper()

        # Region group from the "regional peers" header
        region_group = None
        for h3 in soup.select(".row .header h3"):
            t = (h3.get_text() or "").strip()
            if "regional peers" in t.lower():
                m2 = re.search(r"\(([^)]+)\)\s*$", t)
                if m2:
                    region_group = (m2.group(1) or "").strip()
                break

        return target, region_group
    except Exception:
        return None, None


def _apply_initial_view(m: folium.Map, region_group: str | None, fallback_points=None):
    """
    Sets initial view based on region_group.
    If unknown, falls back to bounds around fallback_points if provided.
    """
    rg = (region_group or "").strip()
    if rg in REGION_GROUP_BOUNDS:
        b = REGION_GROUP_BOUNDS[rg]
        m.fit_bounds(b)
        return

    # Fallback: fit to points
    pts = fallback_points or []
    pts = [(float(a), float(b)) for a, b in pts if a is not None and b is not None]
    if len(pts) >= 2:
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    elif len(pts) == 1:
        m.location = [pts[0][0], pts[0][1]]


# ---------- main ----------
def build_map(target_iata=None, highlight_iatas=None) -> folium.Map:
    """
    Return a folium.Map for ACA airports.

    highlight_iatas: optional set/list of IATA codes to emphasize.
      - Target airport is taken from the argument and gets a red label.
      - Other airports get filled circles with no visible outline.

    Important:
      - We DO NOT restrict to Americas anymore; highlighted international peers keep correct ACA colors.
      - Initial view is set by target's region group parsed from docs/grid.html.
    """
    parsed_target, parsed_region_group = _parse_grid_target_and_region_group(GRID_DEFAULT_PATH)

    target_iata = (target_iata or "").strip().upper()

    # Resolve highlight list
    if not highlight_iatas:
        highlight_list = []
        if parsed_target:
            highlight_list.append(parsed_target)
    else:
        highlight_list = [str(x).upper() for x in (highlight_iatas or [])]

    # Prefer the user-specified target for the red label
    if target_iata:
        chosen = target_iata
        if target_iata not in highlight_list:
            highlight_list.insert(0, target_iata)
    else:
        chosen = highlight_list[0] if highlight_list else (parsed_target or None)

    highlight = set([c for c in highlight_list if c])

    aca_html = fetch_aca_html()
    aca = parse_aca_table(aca_html)
    coords = load_coords()

    # Merge ALL ACA rows with coordinates (global), not just Americas
    aca_all = (
        aca.merge(coords, on="iata", how="left")
           .dropna(subset=["latitude_deg", "longitude_deg"])
           .copy()
    )
    if aca_all.empty:
        raise RuntimeError("No rows after joining ACA table to coordinates.")

    # Build a JSON blob with metadata for all ACA airports (global)
    meta = {}
    for _, row in aca_all.iterrows():
        lvl = row.aca_level if row.aca_level in PALETTE else "Unknown"
        lvl_badge = LEVEL_BADGE.get(lvl, "") if lvl != "Unknown" else ""
        size_key = row.get("size", "small")
        meta[str(row.iata)] = {
            "lat": float(row.latitude_deg),
            "lon": float(row.longitude_deg),
            "lvl": lvl,
            "size": size_key,
            "fill": PALETTE.get(lvl, "#666"),
            "badge": lvl_badge,
            "country": row.get("country", ""),
            "airport": str(row.get("airport") or row.iata),
        }

    # Guarantee all requested highlight codes can render, even if not ACA-scored
    if highlight:
        present_set = set(aca_all["iata"])
        missing = [c for c in highlight_list if c not in present_set]
        if missing:
            extra = coords[coords["iata"].isin(missing)].copy()
            if not extra.empty:
                extra = extra.assign(
                    airport=extra.get("name", extra["iata"]),
                    country=extra.get("iso_country", ""),
                    region="",
                    aca_level="Unknown",
                    region4="",
                )
                keep_cols = list(aca_all.columns)
                for col in keep_cols:
                    if col not in extra.columns:
                        extra[col] = None
                aca_all = pd.concat([aca_all, extra[keep_cols]], ignore_index=True, sort=False)
                aca_all = aca_all.drop_duplicates(subset=["iata"], keep="first")

                for _, row in extra.iterrows():
                    code = str(row.iata)
                    if code in meta:
                        continue
                    meta[code] = {
                        "lat": float(row.latitude_deg),
                        "lon": float(row.longitude_deg),
                        "lvl": "Unknown",
                        "size": row.get("size", "small") if "size" in row else "small",
                        "fill": "#666",
                        "badge": "",
                        "country": row.get("country", ""),
                        "airport": str(row.get("airport") or row.iata),
                    }

    # Plot only the highlight set if present
    plot_df = aca_all[aca_all["iata"].isin(highlight)].copy() if highlight else aca_all.copy()
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

    # Apply initial view based on parsed region group from grid.html
    fallback_pts = list(zip(plot_df["latitude_deg"].tolist(), plot_df["longitude_deg"].tolist()))
    _apply_initial_view(m, parsed_region_group, fallback_points=fallback_pts)

    groups = {
        lvl: folium.FeatureGroup(name=lvl, show=True).add_to(m)
        for lvl in (LEVELS + ["Unknown"])
    }

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
  font-weight: 700; font-size: 12px; letter-spacing: 0.5px;
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
  position:absolute; left:12px; top:112px; z-index:9999;
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
  font-weight:600; text-align:left; white-space:nowrap;
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

    # custom legend
    legend_items = "".join(
        '<div class="row"><span class="dot" style="background:{color}"></span>{lvl}</div>'.format(
            color=PALETTE.get(lvl, "#666"), lvl=lvl
        )
        for lvl in reversed(LEVELS + ["Unknown"])
    )
    legend_html = (
        '<div class="legend-box">'
        '<div class="title">ACA Level</div>'
        f"{legend_items}"
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))

    # Expose metadata to JS
    coords_json = json.dumps(meta, separators=(",", ":"))
    m.get_root().html.add_child(
        folium.Element(
            f'<script id="aca-map-data" type="application/json">{coords_json}</script>'
        )
    )

    # dots + permanent tooltips for the initial highlighted set
    for _, r in plot_df.iterrows():
        lat, lon = float(r.latitude_deg), float(r.longitude_deg)
        size_key = r.get("size", "small")
        base_radius = RADIUS.get(size_key, 6)

        radius = base_radius * 1.5
        stroke_color = "rgba(0,0,0,0)"
        stroke_weight = 0

        fill_opacity = 0.80
        offset_y_base = radius + max(stroke_weight, 0) + max(LABEL_GAP_PX, 1)
        offset_y = -int(offset_y_base * LABEL_OFFSET_SCALE)

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
                    airport=r.airport
                    if pd.notna(r.get("airport")) and str(r.get("airport")).strip()
                    else r.iata,
                    iata=r.iata,
                    lvl=lvl,
                    ctry=r.get("country", ""),
                ),
                max_width=320,
            ),
        )

        if lvl == "Unknown":
            label_text = f"{r.iata}, N/A"
        else:
            lvl_badge = LEVEL_BADGE.get(lvl, "")
            label_text = f"{r.iata}, {lvl_badge}"

        label_color_style = (
            ' style="color:#E74C3C;"' if (chosen and r.iata == chosen) else ""
        )
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

    # JS: zoom meter + clustering + dynamic marker toggling
    js = r"""
(function(){
  try {
    const MAP_NAME = "__MAP_NAME__";
    const CHOSEN   = "__CHOSEN__";
    const ZOOM_SNAP = __ZOOM_SNAP__;
    const ZOOM_DELTA = __ZOOM_DELTA__;
    const WHEEL_PX = __WHEEL_PX__;
    const WHEEL_DEBOUNCE = __WHEEL_DEBOUNCE__;
    const DB_MAX_HISTORY = __DB_MAX_HISTORY__;
    const UPDATE_DEBOUNCE_MS = __UPDATE_DEBOUNCE_MS__;

    const STACK_ON_AT_Z = __STACK_ON_AT_Z__;
    const HIDE_LABELS_BELOW_Z = __HIDE_LABELS_BELOW_Z__;
    const GROUP_RADIUS_MILES = __GROUP_RADIUS_MILES__;

    // Match Python label offset scaling
    const OFFSET_SCALE = 0.7;

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

      // Load full metadata
      let ACA_META = {};
      try {
        const metaScript = document.getElementById('aca-map-data');
        if (metaScript) {
          ACA_META = JSON.parse(metaScript.textContent || "{}");
        }
      } catch(e) {
        ACA_META = {};
      }
      const ACA_MARKERS = {};

      // Register markers that were created by Python initially
      function registerExistingMarkers(){
        map.eachLayer(lyr => {
          if (!(lyr instanceof L.CircleMarker)) return;
          const tt = (lyr.getTooltip && lyr.getTooltip()) || null;
          if (!tt || !tt._container) return;
          const el = tt._container;
          if (!el || !el.classList.contains('iata-tt')) return;
          const cls = Array.from(el.classList);
          const iata = (cls.find(c => c.startsWith('tt-')) || 'tt-').slice(3);
          if (iata) ACA_MARKERS[iata] = lyr;
        });
      }
      registerExistingMarkers();

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
        let maxZ = (map.getMaxZoom && map.getMaxZoom());
        if (maxZ == null) maxZ = 19;
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
          const labelText = items[i].iata + (items[i].level ? (", " + items[i].level) : "");
          if (CHOSEN && items[i].iata === CHOSEN){
            r.innerHTML = '<span style="color:#E74C3C;">' + labelText + '</span>';
          } else {
            r.textContent = labelText;
          }
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
      function scheduleUpdate(){ setTimeout(updateAll, UPDATE_DEBOUNCE_MS); }

      if (map.whenReady) map.whenReady(updateAll);
      map.on('zoomend moveend overlayadd overlayremove layeradd layerremove resize', scheduleUpdate);
      updateAll();

      // Listen for ACA table toggle messages relayed from the parent
      window.addEventListener('message', function(ev){
        const data = ev.data || {};
        if (!data || data.type !== 'ACA_TOGGLE_CODE') return;
        const code = (data.code || '').toUpperCase();
        const active = !!data.active;
        if (!code) return;

        if (active){
          if (!ACA_MARKERS[code]){
            const meta = ACA_META[code];
            if (!meta) return;

            const sizeKey = meta.size || 'small';
            const baseRadius = (sizeKey === 'large' ? 8 : (sizeKey === 'medium' ? 7 : 6));
            const radius = baseRadius * 1.5;
            const strokeColor = "rgba(0,0,0,0)";
            const strokeWeight = 0;
            const fillOpacity = 0.95;
            const offsetBase = radius + Math.max(strokeWeight, 0) + Math.max(5, 1);
            const offsetY = -Math.round(offsetBase * OFFSET_SCALE);

            const lvl = meta.lvl || "Unknown";
            const fillColor = meta.fill || "#666";

            const dot = L.circleMarker(
              [meta.lat, meta.lon],
              {
                radius: radius,
                color: strokeColor,
                weight: strokeWeight,
                fill: true,
                fillColor: fillColor,
                fillOpacity: fillOpacity
              }
            );

            let labelText;
            if (lvl === "Unknown") labelText = code + ", N/A";
            else labelText = code + ", " + (meta.badge || "");

            const labelHtml = '<div class="ttxt">' + labelText + '</div>';

            dot.bindTooltip(labelHtml, {
              permanent:true,
              direction:"top",
              offset:[0, offsetY],
              sticky:false,
              className:"iata-tt size-" + sizeKey + " tt-" + code
            });

            dot.addTo(map);
            ACA_MARKERS[code] = dot;
          }
        } else {
          if (ACA_MARKERS[code]){
            map.removeLayer(ACA_MARKERS[code]);
            delete ACA_MARKERS[code];
          }
        }
        scheduleUpdate();
      });
    }
  } catch (err) {
    console.error("[ACA] init failed:", err);
  }
})();
"""

    js = (
        js.replace("__MAP_NAME__", m.get_name())
        .replace("__ZOOM_SNAP__", str(float(ZOOM_SNAP)))
        .replace("__ZOOM_DELTA__", str(float(ZOOM_DELTA)))
        .replace("__WHEEL_PX__", str(int(WHEEL_PX_PER_ZOOM)))
        .replace("__WHEEL_DEBOUNCE__", str(int(WHEEL_DEBOUNCE_MS)))
        .replace("__DB_MAX_HISTORY__", str(int(DB_MAX_HISTORY)))
        .replace("__UPDATE_DEBOUNCE_MS__", str(int(UPDATE_DEBOUNCE_MS)))
        .replace("__STACK_ON_AT_Z__", str(float(STACK_ON_AT_Z)))
        .replace("__HIDE_LABELS_BELOW_Z__", str(float(HIDE_LABELS_BELOW_Z)))
        .replace("__GROUP_RADIUS_MILES__", str(float(GROUP_RADIUS_MILES)))
        .replace("__CHOSEN__", chosen or "")
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
