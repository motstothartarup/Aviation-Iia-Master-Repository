# scripts/build_aca_table.py
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


def fetch_aca_html(timeout: int = 45) -> str:
    url = "https://www.airportcarbonaccreditation.org/accredited-airports/"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ACA-Table-Bot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_aca_table(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    dfs: List[pd.DataFrame] = []

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
    by_region: Dict[str, Dict[str, List[str]] ] = {}
    for reg in regions:
        sub = df[df["region4"] == reg]
        level_map: Dict[str, List[str]] = {lvl: [] for lvl in LEVELS_DESC}
        for lvl, block in sub.groupby("aca_level"):
            level_map.setdefault(lvl, [])
            codes = sorted(str(x).strip().upper() for x in block["iata"].dropna().unique())
            level_map[lvl].extend(codes)
        by_region[reg] = level_map
    return {"levels_desc": LEVELS_DESC, "regions": regions, "by_region": by_region}


# --- Competitors (Passengers & Share ONLY; Growth excluded) ---
def _parse_grid_competitors_from_html(grid_html: str) -> Dict[str, List[str]]:
    soup = BeautifulSoup(grid_html, "lxml")
    rows = soup.select(".container .row")

    def _cat_from_label(txt: str) -> Optional[str]:
        t = " ".join((txt or "").strip().lower().split())
        if "share of region" in t:
            return "Share"
        if "growth" in t:
            return "Growth"
        if "passenger" in t:
            return "Passengers"
        return None

    comp: Dict[str, List[str]] = {}
    # Keep Share for backward compatibility, but all new grids
    # will just default to "Passengers".
    allowed = {"Share", "Passengers"}

    for row in rows:
        grid_el = row.select_one(".grid")
        if not grid_el:
            continue

        # Old layout: .cat exists and we infer category from its label.
        cat_el = row.select_one(".cat")
        if cat_el is not None:
            cat = _cat_from_label(cat_el.get_text())
        else:
            # New layout: no .cat at all, everything is throughput,
            # so treat as "Passengers" by default.
            cat = "Passengers"

        if not cat or cat not in allowed:
            continue

        chips = grid_el.select(".chip")
        for chip in chips:
            classes = chip.get("class", [])
            if "origin" in classes:
                continue
            code_el = chip.select_one(".code")
            if not code_el:
                continue
            iata = code_el.get_text(strip=True).upper()
            if iata and len(iata) <= 4:
                comp.setdefault(iata, [])
                if cat not in comp[iata]:
                    comp[iata].append(cat)

    return comp


def _discover_competitors_from_grid(grid_html_path: str = GRID_DEFAULT_PATH) -> Dict[str, List[str]]:
    try:
        if not os.path.exists(grid_html_path):
            return {}
        with open(grid_html_path, "r", encoding="utf-8") as f:
            html = f.read()
        return _parse_grid_competitors_from_html(html)
    except Exception:
        return {}


# --- Main HTML builder ---
def build_aca_table_html(
    target_iata: Optional[str] = None,
    competitors: Optional[Dict[str, List[str]]] = None,
    grid_html_path: str = GRID_DEFAULT_PATH,
) -> tuple[str, pd.DataFrame]:

    html = fetch_aca_html()
    df = parse_aca_table(html)
    payload = make_payload(df)

    comp_dict = competitors or {}
    if not comp_dict:
        comp_dict = _discover_competitors_from_grid(grid_html_path)

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data_json = json.dumps(payload, separators=(",", ":"))
    competitors_json = json.dumps(comp_dict, separators=(",", ":"))

    target_iata = (target_iata or "").upper()
    if target_iata and (df["iata"] == target_iata).any():
        default_region = df.loc[df["iata"] == target_iata, "region4"].iloc[0]
    else:
        default_region = (
            "Americas"
            if "Americas" in payload["regions"]
            else (payload["regions"][0] if payload["regions"] else "")
        )

    page = f"""<!doctype html>
<meta charset="utf-8">
<title>ACA Airports — Region Table</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<meta http-equiv="Expires" content="0"/>
<style>
  body {{ margin:0; padding:24px; font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; background:#f6f8fb; }}
  .wrap {{ max-width:1100px; margin:0 auto; }}
  .card {{ background:#fff; border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,.08); padding:20px; }}
  h1 {{ margin:0 0 12px 0; font-size:20px; }}
  .row {{ display:flex; gap:16px; align-items:center; flex-wrap:wrap; }}
  .muted {{ color:#6b7785; font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:14px; font-size:13px; }}
  thead th {{ text-align:left; font-weight:600; padding:6px 8px; border-bottom:1px solid #ddd; background:#fafbfc; }}
  tbody td {{ padding:6px 8px; border-bottom:1px solid #eee; vertical-align:top; }}
  td.lvl {{ font-weight:700; width:100px; white-space:nowrap; }}
  td.count {{ text-align:right; width:60px; color:#6b7785; }}
  td.codes code {{ font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; padding:2px 6px; border-radius:6px; margin:2px 4px 2px 0; display:inline-block; }}
  code.comp {{ background:#fff3cd; color:#000; border:1px solid #f1c40f; }}
  code.hl {{ background:#e74c3c; color:#fff; font-size:14px; font-weight:700; }}
  #downloadBtn {{ margin-top:10px; padding:6px 12px; border-radius:6px; border:none; background:#3498db; color:#fff; cursor:pointer; }}
</style>

<div class="wrap">
  <div class="card" id="captureArea">
    <div class="row">
      <h1>ACA Airports by Region</h1>
      <div class="muted">Last updated: {updated}</div>
    </div>
    <div class="row" style="margin-top:8px;">
      <label for="regionSelect" class="muted">Region:</label>
      <select id="regionSelect" aria-label="Choose region"></select>
    </div>

    <table id="acaTable">
      <thead>
        <tr>
          <th>ACA Level</th>
          <th>Airport Codes</th>
          <th style="text-align:right">Count</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>
  <button id="downloadBtn">Download as JPEG</button>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script id="aca-data" type="application/json">{data_json}</script>
<script id="competitor-data" type="application/json">{competitors_json}</script>
<script>
(function(){{
  const DATA = JSON.parse(document.getElementById('aca-data').textContent);
  const COMP = JSON.parse(document.getElementById('competitor-data').textContent || "{{}}");
  const sel = document.getElementById('regionSelect');
  const tbody = document.querySelector('#acaTable tbody');
  const levels = DATA.levels_desc || [];
  const regions = DATA.regions || [];
  const byRegion = DATA.by_region || {{}};
  const target = "{target_iata}";
  const defaultRegion = "{default_region}";

  function option(v,t){{ const o=document.createElement('option'); o.value=v; o.textContent=t; return o; }}
  regions.forEach(r => sel.appendChild(option(r,r)));
  if (regions.includes(defaultRegion)) sel.value = defaultRegion;

  // Attach click handlers so users can toggle yellow highlight on any code.
  function attachChipHandlers(){{ 
    const chips = document.querySelectorAll('#acaTable tbody td.codes code');
    chips.forEach(chip => {{
      chip.style.cursor = 'pointer';
      chip.addEventListener('click', (ev) => {{
        const el = ev.currentTarget;
        // Keep the primary target (red) fixed.
        if (el.classList.contains('hl')) return;
        el.classList.toggle('comp');
      }});
    }});
  }}

  function render(region){{ 
    tbody.innerHTML = '';
    let total = 0;
    const buckets = byRegion[region] || {{}};
    levels.forEach(lvl => {{
      const codes = (buckets[lvl] || []).slice().sort();
      total += codes.length;
      const tr = document.createElement('tr');
      const tdLvl = document.createElement('td'); tdLvl.className='lvl'; tdLvl.textContent = lvl;
      const tdCodes = document.createElement('td'); tdCodes.className='codes';
      const tdCount = document.createElement('td'); tdCount.className='count'; tdCount.textContent = String(codes.length);
      if (codes.length) {{
        codes.forEach(c => {{
          const chip = document.createElement('code'); 
          chip.textContent = c;
          chip.dataset.code = c;
          if (c === target) chip.classList.add('hl');
          else if (Array.isArray(COMP[c]) && COMP[c].length) chip.classList.add('comp');
          tdCodes.appendChild(chip);
        }});
      }} else {{
        tdCodes.innerHTML = '<span class="muted">—</span>';
      }}
      tr.appendChild(tdLvl); 
      tr.appendChild(tdCodes); 
      tr.appendChild(tdCount);
      tbody.appendChild(tr);
    }});
    const trTotal = document.createElement('tr');
    trTotal.innerHTML = '<td class="lvl">Total</td><td></td><td class="count">' + total + '</td>';
    tbody.appendChild(trTotal);
    attachChipHandlers();
  }}

  sel.addEventListener('change', () => render(sel.value));
  render(sel.value || regions[0] || '');

  // Export as high-res JPEG
  document.getElementById('downloadBtn').addEventListener('click', () => {{
    html2canvas(document.getElementById('captureArea'), {{ scale: 3 }}).then(canvas => {{
      const link = document.createElement('a');
      link.download = 'aca_table.jpeg';
      link.href = canvas.toDataURL('image/jpeg', 1.0);
      link.click();
    }});
  }});
}})();
</script>
"""
    return page, df
