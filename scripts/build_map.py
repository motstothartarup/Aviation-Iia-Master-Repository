# scripts/build_map.py
# ACA Americas map with competitor highlighting and export
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

LEVEL_BADGE = {
    "Level 1": "1", "Level 2": "2", "Level 3": "3", "Level 3+": "3+",
    "Level 4": "4", "Level 4+": "4+", "Level 5": "5",
}

RADIUS = {"large": 8, "medium": 7, "small": 6}
STROKE = 2
LABEL_GAP_PX = 10

# zoom tuning
ZOOM_SNAP = 0.10
ZOOM_DELTA = 0.75
WHEEL_PX_PER_ZOOM = 100
WHEEL_DEBOUNCE_MS = 10

# stacking
DB_MAX_HISTORY = 200
UPDATE_DEBOUNCE_MS = 120
STACK_ON_AT_Z = 7.5
HIDE_LABELS_BELOW_Z = 4.4
GROUP_RADIUS_MILES = 30.0
STACK_ROW_GAP_PX = 6

OUT_DIR = "docs"
OUT_FILE = os.path.join(OUT_DIR, "aca_map.html")


def write_error_page(msg: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = f"""<!doctype html><meta charset="utf-8">
<title>ACA Americas map</title>
<style>body{{font:16px/1.45 sans-serif;padding:24px;color:#233;max-width:900px;margin:auto;background:#f6f8fb}}</style>
<div><h1>ACA Americas map</h1>
<p>Status: unavailable.</p><p>Reason: {msg}</p>
<p>Last attempt: {updated}. This page updates when the generator runs.</p></div>"""
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)


def fetch_aca_html(timeout: int = 45) -> str:
    url = "https://www.airportcarbonaccreditation.org/accredited-airports/"
    headers = {"User-Agent": "Mozilla/5.0 (ACA-Map-Bot/1.0)"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_aca_table(html: str) -> pd.DataFrame:
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
                target = df; break
        if target is None: raise RuntimeError("ACA table not found")
        dfs = [target]
    raw = dfs[0]
    aca = raw.rename(columns={
        "Airport": "airport", "Airport code": "iata",
        "Country": "country", "Region": "region",
        "Level": "aca_level",
    })[["iata", "airport", "country", "region", "aca_level"]]
    def region4(r: str) -> str:
        if r in ("North America", "Latin America & the Caribbean"): return "Americas"
        if r == "UKIMEA": return "Europe"
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


def build_map(highlight_iatas=None) -> folium.Map:
    highlight_list = list(highlight_iatas or [])
    highlight = {str(x).upper() for x in highlight_list}

    aca_html = fetch_aca_html()
    aca = parse_aca_table(aca_html)
    coords = load_coords()
    amer = aca[aca["region4"].eq("Americas")].merge(coords, on="iata", how="left").dropna(subset=["latitude_deg","longitude_deg"])
    if amer.empty: raise RuntimeError("No Americas rows")

    center_lat, center_lon = float(amer["latitude_deg"].mean()), float(amer["longitude_deg"].mean())
    m = folium.Map(tiles="CartoDB Positron", zoomControl=True, prefer_canvas=True,
                   location=[center_lat, center_lon], zoom_start=4.7)

    groups = {lvl: folium.FeatureGroup(name=lvl, show=True).add_to(m) for lvl in LEVELS}
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    BUILD_VER = "r2.0-redsel-yellowcomp"

    # CSS + legend
    css = f"""
<style>
.leaflet-tooltip.iata-tt{{background:transparent;border:0;box-shadow:none;
  color:#000;font:12px 900 'Open Sans',sans-serif;text-transform:uppercase;white-space:nowrap;text-align:center}}
.leaflet-tooltip-top:before,.leaflet-tooltip-bottom:before,.leaflet-tooltip-left:before,.leaflet-tooltip-right:before{{display:none!important}}
.leaflet-tooltip.iata-tt .ttxt{{display:inline-block;line-height:1.05}}
.iata-stack{{position:absolute;z-index:9998;pointer-events:none;background:transparent;border:0;box-shadow:none;
  font:12px 1000 'Open Sans',sans-serif;color:#000;text-transform:uppercase;white-space:nowrap}}
.iata-stack .row{{line-height:1.0;margin:{STACK_ROW_GAP_PX}px 0}}
.legend-box{{position:absolute;left:12px;top:170px;z-index:9999;background:#fff;padding:6px 8px;border-radius:8px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);font:12px 'Open Sans',sans-serif;color:#485260}}
.legend-box .title{{font-weight:600;margin-bottom:4px}}
.legend-box .row{{display:flex;align-items:center;gap:6px;margin:3px 0}}
.legend-box .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;border:1px solid rgba(0,0,0,.25)}}
</style>
<div class="legend-box"><div class="title">ACA Level</div>
{''.join(f'<div class="row"><span class="dot" style="background:{PALETTE[lvl]}"></span>{lvl}</div>' for lvl in reversed(LEVELS))}
</div>
<button id="downloadBtn" style="position:absolute;top:12px;left:12px;z-index:9999;">Download JPEG</button>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
document.getElementById('downloadBtn').addEventListener('click',()=>{{
 const hide=[document.querySelector('.leaflet-control-layers'),
             document.querySelector('.leaflet-control-zoom'),
             document.querySelector('.last-updated')];
 hide.forEach(e=>{{if(e)e.style.display='none';}});
 html2canvas(document.querySelector('.leaflet-container'),{{scale:3}}).then(c=>{{
   const a=document.createElement('a');a.download='aca_map.jpeg';a.href=c.toDataURL('image/jpeg',1.0);a.click();
   hide.forEach(e=>{{if(e)e.style.display='';}});
 }});
}});
</script>
"""
    m.get_root().html.add_child(folium.Element(css))

    # dots
    for idx, r in amer.iterrows():
        lat, lon = float(r.latitude_deg), float(r.longitude_deg)
        base_radius = RADIUS.get(r.get("size","small"),6)
        lvl_badge = LEVEL_BADGE.get(r.aca_level,"")
        if highlight_list and r.iata == highlight_list[0]:
            radius, stroke_color, stroke_weight, fill_opacity, add_label = base_radius*1.6,"#E74C3C",3,0.95,True
        elif r.iata in highlight:
            radius, stroke_color, stroke_weight, fill_opacity, add_label = base_radius*1.5,"#F1C40F",3,0.95,True
        else:
            radius, stroke_color, stroke_weight, fill_opacity, add_label = base_radius*0.75,"transparent",0,0.5,False
        offset_y = -(radius+stroke_weight+LABEL_GAP_PX)
        dot = folium.CircleMarker([lat,lon],radius=radius,color=stroke_color,weight=stroke_weight,
                                  fill=True,fill_color=PALETTE.get(r.aca_level,"#666"),fill_opacity=fill_opacity)
        if add_label:
            label_html=f'<div class="ttxt">{r.iata}, {lvl_badge}</div>'
            dot.add_child(folium.Tooltip(label_html,permanent=True,direction="top",
                          offset=(0,offset_y),sticky=False,
                          class_name=f"iata-tt tt-{r.iata}",parse_html=True))
        dot.add_to(groups[r.aca_level])

    folium.LayerControl(collapsed=False).add_to(m)

    # JS logic (clustering, zoom meter, etc.) patched: show CODE, LEVEL in stacks
    js = f"""
(function() {{
  try {{
    const MAP_NAME="{m.get_name()}";
    function until(c,cb,tries=200,delay=50){{(function t(n){{if(c())return cb();if(n<=0)return;setTimeout(()=>t(n-1),delay);}})(tries);}}
    until(()=>window[MAP_NAME]&&window[MAP_NAME].getPanes,init,200,50);
    function init(){{
      const map=window[MAP_NAME];const pane=map.getPanes().tooltipPane;
      function collectItems(){{const items=[];map.eachLayer(l=>{{
        if(!(l instanceof L.CircleMarker))return;
        const tt=l.getTooltip&&l.getTooltip();if(!tt||!tt._container)return;
        const el=tt._container; if(!el.classList.contains('iata-tt'))return;
        const txt=el.textContent.split(',');const iata=txt[0].trim();const level=(txt[1]||'').trim();
        const pt=map.latLngToContainerPoint(l.getLatLng());const R=el.getBoundingClientRect();
        items.push({{iata,level,el,dot:{{x:pt.x,y:pt.y}},label:{{x:R.x,y:R.y,h:R.height}}}});
      }});return items;}}
      function buildClusters(it,R){{const n=it.length,p=Array.from({{length:n}},(_,i)=>i);function f(a){{return p[a]==a?a:(p[a]=f(p[a]));}}function u(a,b){{a=f(a);b=f(b);if(a!==b)p[b]=a;}}for(let i=0;i<n;i++)for(let j=i+1;j<n;j++){{const dx=it[i].dot.x-it[j].dot.x;const dy=it[i].dot.y-it[j].dot.y;if(dx*dx+dy*dy<=R*R)u(i,j);}}const g=new Map();for(let i=0;i<n;i++){{const r=f(i);if(!g.has(r))g.set(r,[]);g.get(r).push(i);}}return [...g.values()].filter(x=>x.length>1);}}
      function drawStack(g,it){{const d=document.createElement('div');d.className='iata-stack';g.forEach(i=>{{const r=document.createElement('div');r.className='row';r.textContent=it[i].iata+', '+it[i].level;d.appendChild(r);}});pane.appendChild(d);}}
      function apply(){{const it=collectItems();const c=buildClusters(it,40);c.forEach(g=>g.forEach(i=>{{it[i].el.style.display='none';}}));c.forEach(g=>drawStack(g,it));}}
      map.on('zoomend moveend',apply);apply();
    }}
  }}catch(e){{console.error(e);}}
}})();
"""
    m.get_root().script.add_child(folium.Element(js))
    return m


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        fmap = build_map()
        fmap.save(OUT_FILE)
        print("Wrote", OUT_FILE)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        write_error_page(str(e))
        sys.exit(0)
