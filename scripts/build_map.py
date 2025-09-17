# scripts/build_map.py
# ACA Americas map with:
#  - optional highlighting (bigger dot + outline) for a set of IATA codes
#  - HIGH-RES JPEG export of the CURRENT VIEW (keeps basemap), hiding UI while capturing

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
LEVELS_ALL = LEVELS + ['Unknown']  # ensure we can plot extras not in ACA table

PALETTE = {
    "Level 1": "#5B2C6F",
    "Level 2": "#00AEEF",
    "Level 3": "#1F77B4",
    "Level 3+": "#2ECC71",
    "Level 4": "#F4D03F",
    "Level 4+": "#E39A33",
    "Level 5": "#E74C3C",
    "Unknown": "#666666",
}

LEVEL_BADGE = {
    "Level 1": "1",
    "Level 2": "2",
    "Level 3": "3",
    "Level 3+": "3+",
    "Level 4": "4",
    "Level 4+": "4+",
    "Level 5": "5",
    "Unknown": "–",
}

RADIUS = {"large": 8, "medium": 7, "small": 6}
STROKE = 2
LABEL_GAP_PX = 10

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
HIDE_LABELS_BELOW_Z = 4.4

# --- Grouping distance in miles ---
GROUP_RADIUS_MILES = 30.0
STACK_ROW_GAP_PX = 6

OUT_DIR = "docs"
OUT_FILE = os.path.join(OUT_DIR, "aca_map.html")
GRID_DEFAULT_PATH = os.path.join("docs", "grid.html")  # read Composite 7 from grid

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
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ACA-Map-Bot/1.0)",
               "Accept": "text/html,application/xhtml+xml"}
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

# ---- read target + 7 Composite from grid ----
def _parse_grid_composite7(grid_html_path: str = GRID_DEFAULT_PATH):
    try:
        if not os.path.exists(grid_html_path):
            return None, []
        with open(grid_html_path, "r", encoding="utf-8") as f:
            html = f.read()
        soup = BeautifulSoup(html, "lxml")

        h = soup.select_one(".header h3")
        target = None
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
    highlight_iatas:
      - FIRST item = chosen (red outline)
      - remaining = competitors (yellow outline)
    If not provided, we read [target + composite-7] from docs/grid.html.
    """
    if not highlight_iatas:
        tgt, comp7 = _parse_grid_composite7(GRID_DEFAULT_PATH)
        if tgt and comp7:
            highlight_iatas = [tgt] + comp7

    # keep order + a set for membership tests
    highlight_list = list(highlight_iatas or [])
    highlight = {str(x).upper() for x in highlight_list}
    chosen = highlight_list[0].upper() if highlight_list else None

    aca_html = fetch_aca_html()
    aca = parse_aca_table(aca_html)
    coords = load_coords()

    # Base layer: ACA Americas joined with coords
    amer = (
        aca[aca["region4"].eq("Americas")]
        .merge(coords, on="iata", how="left")
        .dropna(subset=["latitude_deg", "longitude_deg"])
    )

    # >>> Ensure ALL 7 highlighted codes render <<<
    # For any highlighted IATA not present in `amer`, try to add from coords anyway,
    # tagging them as aca_level='Unknown' so they have a bucket and a color.
    missing = []
    if highlight_list:
        present_set = set(amer["iata"])
        for code in highlight_list:
            if code not in present_set:
                missing.append(code)

    if missing:
        extra = coords[coords["iata"].isin(missing)].copy()
        # If coords missing for any, they simply can't be plotted.
        if not extra.empty:
            extra = extra.assign(
                airport=extra.get("name", extra["iata"]),
                country=extra.get("iso_country", ""),
                region="",
                aca_level="Unknown",
                region4="Americas",  # keep them visible with rest of map extent
            )
            # align columns present in amer
            keep_cols = list(amer.columns)
            # make sure all needed columns exist
            for col in keep_cols:
                if col not in extra.columns:
                    extra[col] = None
            amer = pd.concat([amer, extra[keep_cols]], ignore_index=True)
            amer = amer.drop_duplicates(subset=["iata"], keep="first")

    # After augmentation, filter highlight list only by availability of coordinates
    if highlight_list:
        present = set(amer["iata"])
        highlight_list = [x for x in highlight_list if x in present]
        highlight = {x for x in highlight_list}
        chosen = highlight_list[0] if highlight_list else None

    # Center & map
    center_lat = float(amer["latitude_deg"].mean())
    center_lon = float(amer["longitude_deg"].mean())

    m = folium.Map(
        tiles="CartoDB Positron",
        zoomControl=True,
        prefer_canvas=True,
        location=[center_lat, center_lon],
        zoom_start=4.7,
    )

    groups = {lvl: folium.FeatureGroup(name=lvl, show=True).add_to(m) for lvl in LEVELS_ALL}

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    BUILD_VER = "r1.11-composite7-always-show"

    # --- CSS + footer badge + zoom meter + stack styles ---
    badge_html = (
        r"""
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<meta http-equiv="Expires" content="0"/>
<style>
.leaflet-tooltip.iata-tt{ background:transparent;border:0;box-shadow:none;color:#2e2e2e;
  font-family:"Open Sans","Helvetica Neue",Arial,sans-serif;font-weight:900;font-size:12px;letter-spacing:.5px;
  text-transform:uppercase;white-space:nowrap;text-align:center;}
.leaflet-tooltip-top:before,.leaflet-tooltip-bottom:before,.leaflet-tooltip-left:before,.leaflet-tooltip-right:before{display:none!important}
.leaflet-tooltip.iata-tt .ttxt{display:inline-block;line-height:1.05}
.leaflet-tooltip.iata-tt .lvlchip{display:none}
.leaflet-tooltip.iata-tt .iata{display:none}
.leaflet-control-layers-expanded{box-shadow:0 4px 14px rgba(0,0,0,.12);border-radius:10px}
.last-updated{position:absolute;right:12px;bottom:12px;z-index:9999;background:#fff;padding:6px 8px;border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);font:12px "Open Sans","Helvetica Neue",Arial,sans-serif;color:#485260;}
.zoom-meter{position:absolute;left:12px;top:112px;z-index:9999;background:#fff;padding:6px 8px;border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);font:12px "Open Sans","Helvetica Neue",Arial,sans-serif;color:#485260;user-select:none;pointer-events:none;}
.iata-stack{position:absolute;z-index:9998;pointer-events:none;background:transparent;border:0;box-shadow:none;
  font:12px "Open Sans","Helvetica Neue",Arial,sans-serif;color:#000;letter-spacing:.5px;text-transform:uppercase;font-weight:1000;text-align:left;white-space:nowrap;}
.iata-stack .row{line-height:1.0;margin: __ROWGAP__px 0;}
.legend-box{position:absolute;left:12px;top:170px;z-index:9999;background:#fff;padding:6px 8px;border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);font:12px "Open Sans","Helvetica Neue",Arial,sans-serif;color:#485260;user-select:none;}
.legend-box .title{font-weight:600;margin-bottom:4px;}
.legend-box .row{display:flex;align-items:center;gap:6px;margin:3px 0;}
.legend-box .dot{width:10px;height:10px;border-radius:50%;display:inline-block;border:1px solid rgba(0,0,0,.25);}
#downloadBtn{position:absolute;top:12px;left:12px;z-index:9999;background:#3498db;color:#fff;border:none;border-radius:6px;padding:6px 12px;cursor:pointer;}
</style>
<div class="last-updated">Last updated: __UPDATED__ • __VER__</div>
<div id="zoomMeter" class="zoom-meter">Zoom: --%</div>
<button id="downloadBtn" type="button">Download as JPEG</button>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
""".replace("__UPDATED__", updated).replace("__VER__", BUILD_VER).replace("__ROWGAP__", str(int(STACK_ROW_GAP_PX)))
    )
    m.get_root().html.add_child(folium.Element(badge_html))

    # legend (ACA only; we keep Unknown off the legend)
    legend_items = "".join(
        f'<div class="row"><span class="dot" style="background:{PALETTE.get(lvl, "#666")}"></span>{lvl}</div>'
        for lvl in reversed(LEVELS)
    )
    legend_html = f'<div class="legend-box"><div class="title">ACA Level</div>{legend_items}</div>'
    m.get_root().html.add_child(folium.Element(legend_html))

    # --- draw points ---
    for _, r in amer.iterrows():
        lat, lon = float(r.latitude_deg), float(r.longitude_deg)
        size_key = r.get("size", "small")
        base_radius = RADIUS.get(size_key, 6)

        if chosen and r.iata == chosen:
            radius = base_radius * 1.5; stroke_color = "#E74C3C"; stroke_weight = max(STROKE, 3); fill_opacity = 0.95; add_label = True
        elif r.iata in highlight:
            radius = base_radius * 1.5; stroke_color = "#F1C40F"; stroke_weight = max(STROKE, 3); fill_opacity = 0.95; add_label = True
        else:
            radius = base_radius * 0.75; stroke_color = "transparent"; stroke_weight = 0; fill_opacity = 0.50; add_label = False

        offset_y = -(radius + max(stroke_weight, 0) + max(LABEL_GAP_PX, 1))
        level = r.aca_level if r.aca_level in PALETTE else "Unknown"

        dot = folium.CircleMarker(
            [lat, lon],
            radius=float(radius),
            color=stroke_color,
            weight=int(stroke_weight),
            fill=True,
            fill_color=PALETTE.get(level, "#666"),
            fill_opacity=float(fill_opacity),
            popup=folium.Popup(
                "<b>{airport}</b><br>IATA: {iata}<br>ACA: <b>{lvl}</b><br>Country: {ctry}".format(
                    airport=r.airport, iata=r.iata, lvl=level, ctry=r.get("country","")
                ),
                max_width=320,
            ),
        )

        if add_label:
            label_html = f'<div class="ttxt">{r.iata}, {LEVEL_BADGE.get(level, "–")}</div>'
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

        # send to appropriate group (Unknown if not in LEVELS)
        grp = level if level in LEVELS else "Unknown"
        dot.add_to(groups[grp])

    folium.LayerControl(collapsed=False).add_to(m)

    # --- JS helpers (zoom meter, clustering, export) ---
    js = r"""
(function(){
  try{
    const MAP_NAME="__MAP_NAME__",ZOOM_SNAP=__ZOOM_SNAP__,ZOOM_DELTA=__ZOOM_DELTA__,WHEEL_PX=__WHEEL_PX__,WHEEL_DEBOUNCE=__WHEEL_DEBOUNCE__,
          UPDATE_DEBOUNCE_MS=__UPDATE_DEBOUNCE_MS__,STACK_ON_AT_Z=__STACK_ON_AT_Z__,HIDE_LABELS_BELOW_Z=__HIDE_LABELS_BELOW_Z__,GROUP_RADIUS_MILES=__GROUP_RADIUS_MILES__;
    function until(c,cb,n=200,d=50){(function t(i){if(c())return cb();if(i<=0)return;setTimeout(()=>t(i-1),d)})(n)}
    until(()=>typeof window[MAP_NAME]!=="undefined"&&window[MAP_NAME]&&window[MAP_NAME].getPanes&&window[MAP_NAME].getContainer,init,200,50);
    function init(){
      const map=window[MAP_NAME];
      map.options.zoomSnap=ZOOM_SNAP; map.options.zoomDelta=ZOOM_DELTA; map.options.wheelPxPerZoomLevel=WHEEL_PX; map.options.wheelDebounceTime=WHEEL_DEBOUNCE;
      if(map.scrollWheelZoom){ map.scrollWheelZoom.disable(); map.scrollWheelZoom.enable(); }
      const meter=document.getElementById('zoomMeter');
      function updateMeter(){ if(!meter) return; const z=map.getZoom(),minZ=map.getMinZoom?.()||0,maxZ=map.getMaxZoom?.()??19;
        const pct=Math.round(((z-minZ)/Math.max(1e-6,(maxZ-minZ)))*100); meter.textContent=`Zoom: ${pct}% (z=${z.toFixed(2)})`; }
      function milesToPixels(mi){const m=mi*1609.344,c=map.getCenter(),p1=map.latLngToContainerPoint(c),p2=L.point(p1.x+100,p1.y),
        ll2=map.containerPointToLatLng(p2),mPer100=map.distance(c,ll2),pxPerM=100/Math.max(1e-6,mPer100);return m*pxPerM;}
      function clearStacks(){ map.getPanes().tooltipPane.querySelectorAll('.iata-stack').forEach(n=>n.remove()); }
      function showAll(){ map.getPanes().tooltipPane.querySelectorAll('.iata-tt').forEach(el=>el.style.display=''); }
      function hideAll(){ map.getPanes().tooltipPane.querySelectorAll('.iata-tt').forEach(el=>el.style.display='none'); }
      function collect(){ const pane=map.getPanes().tooltipPane,prect=pane.getBoundingClientRect();
        function rect(el){const r=el.getBoundingClientRect();return {x:r.left-prect.left,y:r.top-prect.top,w:r.width,h:r.height};}
        const items=[]; map.eachLayer(lyr=>{ if(!(lyr instanceof L.CircleMarker))return; const tt=lyr.getTooltip?.(); if(!tt||!tt._container)return;
          const el=tt._container; if(!el.classList.contains('iata-tt'))return; el.style.display=''; const ll=lyr.getLatLng(),pt=map.latLngToContainerPoint(ll);
          const cls=[...el.classList], size=(cls.find(c=>c.startsWith('size-'))||'size-small').slice(5), iata=(cls.find(c=>c.startsWith('tt-'))||'tt-').slice(3);
          const txt=(el.textContent||'').split(','), level=(txt.length>1?txt[1].trim():''); const R0=el.querySelector('.ttxt')||el, rct=rect(R0);
          items.push({iata,level,size,el,dot:{x:pt.x,y:pt.y},label:{x:rct.x,y:rct.y,w:rct.w,h:rct.h}}); });
        return items;
      }
      function buildClusters(items,rad){const n=items.length,p=[...Array(n).keys()];const f=a=>p[a]===a?a:(p[a]=f(p[a]));const u=(a,b)=>{a=f(a);b=f(b);if(a!==b)p[b]=a;}
        const R2=rad*rad; for(let i=0;i<n;i++){for(let j=i+1;j<n;j++){const dx=items[i].dot.x-items[j].dot.x,dy=items[i].dot.y-items[j].dot.y;
          if(dx*dx+dy*dy<=R2)u(i,j);}} const g=new Map(); for(let i=0;i<n;i++){const r=f(i); if(!g.has(r))g.set(r,[]); g.get(r).push(i);} return [...g.values()].filter(v=>v.length>=2); }
      function drawStack(idxs,items){const pane=map.getPanes().tooltipPane,div=document.createElement('div');div.className='iata-stack';
        const sorted=idxs.slice().sort((a,b)=>items[a].label.y-items[b].label.y),anchor=items[sorted[0]];
        sorted.forEach(i=>{const r=document.createElement('div');r.className='row';r.textContent=items[i].iata+(items[i].level?`, ${items[i].level}`:'');div.appendChild(r);});
        pane.appendChild(div); requestAnimationFrame(()=>{const sr=div.getBoundingClientRect(),extraH=Math.max(0,sr.height-anchor.label.h);
          div.style.left=Math.round(anchor.label.x)+'px'; div.style.top=Math.round(anchor.label.y-extraH)+'px';}); }
      function apply(items){ clearStacks(); showAll(); const z=map.getZoom(); if(z<HIDE_LABELS_BELOW_Z){ hideAll(); return; } if(z>STACK_ON_AT_Z) return;
        const radPx=milesToPixels(GROUP_RADIUS_MILES); const clusters=buildClusters(items,radPx); clusters.forEach(g=>{ g.forEach(i=>items[i].el.style.display='none'); drawStack(g,items); }); }
      function update(){ updateMeter(); requestAnimationFrame(()=>requestAnimationFrame(()=>{ const items=collect(); apply(items); })); }
      function schedule(){ setTimeout(update, UPDATE_DEBOUNCE_MS); }
      map.whenReady?.(update); map.on('zoomend moveend overlayadd overlayremove layeradd layerremove resize', schedule);
      // export
      const btn=document.getElementById('downloadBtn');
      btn?.addEventListener('click', ()=>{ const hide=[
          document.querySelector('.leaflet-control-layers'),document.querySelector('.leaflet-control-zoom'),
          document.querySelector('.last-updated'),document.getElementById('zoomMeter'),btn];
        hide.forEach(e=>{ if(e){ e.dataset._prevDisplay=e.style.display; e.style.display='none'; }});
        const mapDiv=document.getElementById(MAP_NAME); if(!mapDiv) return;
        html2canvas(mapDiv,{scale:3}).then(canvas=>{ const a=document.createElement('a'); a.download='aca_map.jpeg'; a.href=canvas.toDataURL('image/jpeg',1.0); a.click();
          hide.forEach(e=>{ if(e){ e.style.display=e.dataset._prevDisplay||''; }}); }); });
    }
  }catch(e){ console.error('[ACA] init failed:',e); }
})();
""".replace("__MAP_NAME__", m.get_name())
     .replace("__ZOOM_SNAP__", str(float(ZOOM_SNAP)))
     .replace("__ZOOM_DELTA__", str(float(ZOOM_DELTA)))
     .replace("__WHEEL_PX__", str(int(WHEEL_PX_PER_ZOOM)))
     .replace("__WHEEL_DEBOUNCE__", str(int(WHEEL_DEBOUNCE_MS)))
     .replace("__UPDATE_DEBOUNCE_MS__", str(int(UPDATE_DEBOUNCE_MS)))
     .replace("__STACK_ON_AT_Z__", str(float(STACK_ON_AT_Z)))
     .replace("__HIDE_LABELS_BELOW_Z__", str(float(HIDE_LABELS_BELOW_Z)))
     .replace("__GROUP_RADIUS_MILES__", str(float(GROUP_RADIUS_MILES)))
    )

    m.get_root().html.add_child(folium.Element(js))
    return m

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        fmap = build_map()  # auto-pulls [target + composite-7] and guarantees they render
        fmap.save(OUT_FILE)
        print("Wrote", OUT_FILE)
    except Exception as e:
        print("ERROR building map:", e, file=sys.stderr)
        write_error_page(str(e))
        sys.exit(0)
