# scripts/build_aca_table.py
# Builds ACA region table with competitor annotations from the Grid.

import io
import os
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

LEVELS_DESC = ['Level 5', 'Level 4+', 'Level 4', 'Level 3+', 'Level 3', 'Level 2', 'Level 1']
GRID_DEFAULT_PATH = os.path.join("docs", "grid.html")

# ----------------------------------------------------------
#  Fetch ACA Data
# ----------------------------------------------------------

def fetch_aca_html(timeout: int = 45) -> str:
    url = "https://www.airportcarbonaccreditation.org/accredited-airports/"
    headers = {"User-Agent": "Mozilla/5.0 (ACA-Table-Bot/1.1)",
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
        dfs = pd.read_html(io.StringIO(str(table)))
    if not dfs:
        all_tables = pd.read_html(html)
        want = {"airport", "airport code", "country", "region", "level"}
        for df in all_tables:
            if want.issubset({str(c).strip().lower() for c in df.columns}):
                dfs = [df]
                break
    if not dfs:
        raise RuntimeError("ACA table not found on the page.")

    raw = dfs[0]
    aca = raw.rename(columns={
        "Airport": "airport",
        "Airport code": "iata",
        "Country": "country",
        "Region": "region",
        "Level": "aca_level",
    })[["iata", "airport", "country", "region", "aca_level"]]

    def region4(r: str) -> str:
        if r in ("North America", "Latin America & the Caribbean"):
            return "Americas"
        if r == "UKIMEA":
            return "Europe"
        return r

    aca["region4"] = aca["region"].map(region4)
    aca = aca.dropna(subset=["iata", "aca_level", "region4"]).copy()
    aca["iata"] = aca["iata"].astype(str).str.upper()
    return aca

def make_payload(df: pd.DataFrame) -> dict:
    regions = sorted(df["region4"].unique(), key=lambda x: (x != "Americas", x))
    by_region = {}
    for reg in regions:
        sub = df[df["region4"] == reg]
        level_map = {lvl: [] for lvl in LEVELS_DESC}
        for lvl, block in sub.groupby("aca_level"):
            codes = sorted(str(x).strip().upper() for x in block["iata"].dropna().unique())
            level_map[lvl].extend(codes)
        by_region[reg] = level_map
    return {"levels_desc": LEVELS_DESC, "regions": regions, "by_region": by_region}

# ----------------------------------------------------------
#  Grid Parsing (competitors + % deltas)
# ----------------------------------------------------------

def _category_from_label(text: str) -> Optional[str]:
    t = " ".join((text or "").strip().lower().split())
    if "composite" in t: return "Composite"
    if "share" in t: return "Share"
    if "growth" in t: return "Growth"
    if "passenger" in t: return "Passengers"
    return None

def _parse_grid_competitors(grid_html: str) -> Dict[str, Dict[str, str]]:
    """
    Parse docs/grid.html from build_grid.py and extract competitor IATAs + deltas.
    Returns: { IATA: { "Passengers":"+3.2%", "Growth":"-1.1%", ... } }
    """
    soup = BeautifulSoup(grid_html, "lxml")
    comp: Dict[str, Dict[str, str]] = {}

    for row in soup.select(".container .row"):
        cat_el = row.select_one(".cat")
        grid_el = row.select_one(".grid")
        if not cat_el or not grid_el: continue
        cat = _category_from_label(cat_el.get_text())
        if not cat: continue

        for chip in grid_el.select(".chip"):
            if "origin" in chip.get("class", []):  # skip target itself
                continue
            code_el = chip.select_one(".code")
            dev_el  = chip.select_one(".dev")
            if not code_el: continue
            iata = code_el.get_text(strip=True).upper()
            delta = dev_el.get_text(strip=True) if dev_el else ""
            comp.setdefault(iata, {})[cat] = delta

    return comp

def discover_competitors(grid_html_path: str = GRID_DEFAULT_PATH) -> Dict[str, Dict[str, str]]:
    if not os.path.exists(grid_html_path): return {}
    try:
        with open(grid_html_path, "r", encoding="utf-8") as f:
            html = f.read()
        return _parse_grid_competitors(html)
    except Exception:
        return {}

# ----------------------------------------------------------
#  Builder
# ----------------------------------------------------------

def build_aca_table_html(target_iata: Optional[str] = None,
                         grid_html_path: str = GRID_DEFAULT_PATH) -> tuple[str, pd.DataFrame]:
    """Return (html, aca_df)."""
    # 1) scrape ACA
    html = fetch_aca_html()
    df = parse_aca_table(html)
    payload = make_payload(df)

    # 2) competitors from grid
    competitors = discover_competitors(grid_html_path)

    # 3) region
    target_iata = (target_iata or "").upper()
    if target_iata and (df["iata"] == target_iata).any():
        default_region = df.loc[df["iata"] == target_iata, "region4"].iloc[0]
    else:
        default_region = "Americas" if "Americas" in payload["regions"] else payload["regions"][0]

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data_json = json.dumps(payload, separators=(",", ":"))
    competitors_json = json.dumps(competitors, separators=(",", ":"))

    page = f"""<!doctype html>
<meta charset="utf-8">
<title>ACA Airports — Region Table</title>
<style>
  body {{ margin:0; padding:24px; font:16px/1.45 -apple-system,BlinkMacSystemFont,Roboto,Arial,sans-serif; background:#f6f8fb; color:#333; }}
  .wrap {{ max-width:1100px; margin:0 auto; }}
  .card {{ background:#fff; border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,.08); padding:20px; }}
  h1 {{ margin:0 0 12px 0; font-size:22px; }}
  table {{ width:100%; border-collapse:separate; border-spacing:0; margin-top:14px; font-size:14px; }}
  thead th {{ text-align:left; padding:8px 10px; background:#fafbfc; border-bottom:1px solid #ddd; }}
  td {{ padding:8px 10px; border-bottom:1px solid #eee; vertical-align:top; }}
  td.lvl {{ font-weight:700; width:110px; }}
  code {{ font-family: ui-monospace,monospace; font-size:13px; background:#f5f7fb; padding:2px 6px; border-radius:6px; margin:2px 4px 2px 0; display:inline-block; }}
  code.hl {{ outline:2px solid #E74C3C; outline-offset:1px; }}
  code.comp {{ background:#fff9c4; border:1px solid #fbc02d; }}
  .muted {{ font-size:12px; color:#666; }}
</style>

<div class="wrap">
  <div class="card">
    <h1>ACA Airports by Region</h1>
    <div class="muted">Last updated {updated}</div>
    <table id="acaTable"><thead><tr><th>ACA Level</th><th>Airport Codes</th><th>Count</th></tr></thead><tbody></tbody></table>
  </div>
</div>

<script id="aca-data" type="application/json">{data_json}</script>
<script id="comp-data" type="application/json">{competitors_json}</script>
<script>
(function(){{
  const DATA = JSON.parse(document.getElementById('aca-data').textContent);
  const COMPS = JSON.parse(document.getElementById('comp-data').textContent);
  const tbody = document.querySelector('#acaTable tbody');
  const target = "{target_iata}";
  const defaultRegion = "{default_region}";
  const levels = DATA.levels_desc || [];
  const buckets = (DATA.by_region[defaultRegion] || {});

  levels.forEach(lvl => {{
    const codes = (buckets[lvl] || []).slice().sort();
    let tr = document.createElement('tr');
    let tdLvl = document.createElement('td'); tdLvl.className='lvl'; tdLvl.textContent=lvl;
    let tdCodes = document.createElement('td');
    let tdCount = document.createElement('td'); tdCount.textContent = codes.length;
    codes.forEach(c => {{
      let chip = document.createElement('code'); chip.textContent = c;
      if(c===target) chip.classList.add('hl');
      if(COMPS[c]) {{
        chip.classList.add('comp');
        // append tags with deltas
        let tags = [];
        for (const [cat,val] of Object.entries(COMPS[c])) {{
          tags.push(cat + (val? " " + val : ""));
        }}
        if(tags.length) chip.textContent = c + " ["+tags.join(", ")+"]";
      }}
      tdCodes.appendChild(chip);
    }});
    tr.appendChild(tdLvl); tr.appendChild(tdCodes); tr.appendChild(tdCount);
    tbody.appendChild(tr);
  }});
}})();
</script>
"""
    return page, df
