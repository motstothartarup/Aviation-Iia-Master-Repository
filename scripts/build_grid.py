# scripts/build_aca_table.py
# Library-style builder: scrape ACA, build region board HTML, return html + df.
# Now also computes Top-5 competitors vs target (Passengers / Growth / Share)
# and annotates codes with icon+delta badges per category.

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as pd  # <-- keep as 'pd' for compatibility
import pandas as pd
import requests
from bs4 import BeautifulSoup
import numpy as np

# --- ACI loader helpers (copied to avoid touching build_grid.py) ---
FAA_REGIONS = {
    "Alaskan":{"AK"},
    "New England":{"ME","NH","VT","MA","RI","CT"},
    "Eastern":{"NY","NJ","PA","DE","MD","DC","VA","WV"},
    "Southern":{"KY","TN","NC","SC","GA","FL","PR","VI"},
    "Great Lakes":{"OH","MI","IN","IL","WI"},
    "Central":{"MN","IA","MO","ND","SD","NE","KS"},
    "Southwest":{"NM","TX","OK","AR","LA"},
    "Northwest Mountain":{"WA","OR","ID","MT","WY","UT","CO"},
    "Western-Pacific":{"CA","NV","AZ","HI","GU"},
}

def _norm(s): return re.sub(r"\s+"," ",str(s)).strip().lower()
def _pick(df, cands):
    for c in cands:
        if c in df.columns: return c

def _load_aci(excel_path: str) -> pd.DataFrame:
    raw = pd.read_excel(excel_path, header=2)
    df = raw.rename(columns={c:_norm(c) for c in raw.columns}).copy()
    c_country   = _pick(df, ["country"])
    c_citystate = _pick(df, ["city/state","citystate","city, state","city / state"])
    c_airport   = _pick(df, ["airport name","airport"])
    c_iata      = _pick(df, ["airport code","iata","code"])
    c_total     = _pick(df, ["total passengers","passengers total","total pax"])
    c_yoy       = _pick(df, ["% chg 2024-2023","% chg 2024 - 2023","% chg 2023-2022","yoy %","% change"])
    if c_country:
        df = df[df[c_country].astype(str).str.contains("United States", case=False, na=False)]

    def _state(s):
        if not isinstance(s,str): return None
        parts = re.split(r"\s+", s.strip())
        return parts[-1] if parts else None

    df["state"] = df[c_citystate].apply(_state) if c_citystate else None
    df["name"]  = df[c_airport].astype(str)
    df["iata"]  = df[c_iata].astype(str).str.upper()
    df["total_passengers"] = pd.to_numeric(df[c_total], errors="coerce")
    df["yoy_growth_pct"]   = pd.to_numeric(df[c_yoy], errors="coerce") if c_yoy else np.nan
    df = df.dropna(subset=["iata","state","total_passengers"]).reset_index(drop=True)

    def _faa(st):
        s = str(st).upper()
        for reg, states in FAA_REGIONS.items():
            if s in states: return reg
        return "Unknown"

    df["faa_region"] = df["state"].apply(_faa)
    region_totals = df.groupby("faa_region")["total_passengers"].sum().rename("region_total")
    df = df.merge(region_totals, on="faa_region", how="left")
    df["share_of_region_pct"] = (df["total_passengers"] / df["region_total"] * 100).round(2)
    return df

def _dev(val, target, pct):
    if pd.isna(val) or pd.isna(target): return ""
    diff = float(val) - float(target)
    if pct:
        if abs(target) < 1e-9: return f"{diff:+.1f}pp"
        return f"{(diff/target)*100:+.1f}%"
    if abs(target) < 1e-9: return ""
    return f"{(diff/target)*100:+.1f}%"

def _nearest_sets(df, iata, topn=5):
    """Return dicts of top-5 neighbors by passengers, growth, share, plus target row."""
    t = df.loc[df["iata"]==iata].iloc[0]
    cand = df[df["iata"]!=iata].copy()

    # passengers
    r_pax = cand.assign(abs_diff_pax=(cand["total_passengers"]-t["total_passengers"]).abs()) \
                .sort_values("abs_diff_pax").head(topn)

    # growth (median fallback)
    g = pd.to_numeric(cand["yoy_growth_pct"], errors="coerce")
    g_med = g.median()
    tg = t["yoy_growth_pct"] if pd.notna(t["yoy_growth_pct"]) else g_med
    r_g = cand.assign(yoy_growth_pct=g.fillna(g_med),
                      abs_diff_growth=(g.fillna(g_med)-tg).abs()) \
              .sort_values("abs_diff_growth").head(topn)

    # share
    r_s = cand.assign(abs_diff_share=(cand["share_of_region_pct"]-t["share_of_region_pct"]).abs()) \
              .sort_values("abs_diff_share").head(topn)

    return t, r_pax, r_g, r_s, tg

# ------------------------------------------------------------------------

LEVELS_DESC = ['Level 5', 'Level 4+', 'Level 4', 'Level 3+', 'Level 3', 'Level 2', 'Level 1']

def fetch_aca_html(timeout: int = 45) -> str:
    url = "https://www.airportcarbonaccreditation.org/accredited-airports/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ACA-Table-Bot/1.1)",
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

def make_payload(aca_df: pd.DataFrame,
                 badges: dict,
                 target_iata: str) -> dict:
    """Assemble JSON for the page (levels by region + competitor badges)."""
    regions = sorted(aca_df["region4"].unique(), key=lambda x: (x != "Americas", x))
    by_region = {}
    for reg in regions:
        sub = aca_df[aca_df["region4"] == reg]
        level_map = {lvl: [] for lvl in LEVELS_DESC}
        for lvl, block in sub.groupby("aca_level"):
            level_map.setdefault(lvl, [])
            codes = sorted(str(x).strip().upper() for x in block["iata"].dropna().unique())
            level_map[lvl].extend(codes)
        by_region[reg] = level_map
    return {
        "levels_desc": LEVELS_DESC,
        "regions": regions,
        "by_region": by_region,
        "badges": badges,          # {"IATA":[{"k":"pax"|"growth"|"share"|"target","t":"+3.1%"}]}
        "target": target_iata,     # e.g., "LAX"
    }

def _fmt_pct(x, signed=False, decimals=1):
    if pd.isna(x): return "-"
    val = float(x)
    sign = "+" if (signed and val>=0) else ""
    return f"{sign}{val:.{decimals}f}%"

def _build_badges_from_aci(excel_path: str, target_iata: str) -> dict:
    """Build a mapping of IATA -> list of badge dicts {'k':key,'t':text}."""
    df = _load_aci(excel_path)
    if df[df["iata"]==target_iata].empty:
        return {}
    t, r_pax, r_g, r_s, tg = _nearest_sets(df, target_iata, topn=5)

    badges: dict[str, list] = {}

    # Target badge
    badges.setdefault(target_iata, []).append({"k":"target","t":""})

    # Passengers (compare totals vs target)
    for _, r in r_pax.iterrows():
        d = _dev(r["total_passengers"], t["total_passengers"], pct=False)
        badges.setdefault(str(r["iata"]), []).append({"k":"pax","t": d})

    # Growth (compare YoY vs target)
    for _, r in r_g.iterrows():
        d = _dev(r["yoy_growth_pct"], tg, pct=True)
        badges.setdefault(str(r["iata"]), []).append({"k":"growth","t": d})

    # Share (compare share vs target)
    for _, r in r_s.iterrows():
        d = _dev(r["share_of_region_pct"], t["share_of_region_pct"], pct=True)
        badges.setdefault(str(r["iata"]), []).append({"k":"share","t": d})

    return badges

def build_aca_table_html(target_iata: str | None = None,
                         aci_excel_path: str = "data/ACI_2024_NA_Traffic.xlsx") -> tuple[str, pd.DataFrame]:
    """Return (html, aca_df). If target_iata is provided, default to its region and highlight it.
       Also annotates codes with competitor badges (top-5 per category) vs target airport.
    """
    target_iata = (target_iata or "").upper()

    # 1) Fetch + parse ACA
    html = fetch_aca_html()
    aca_df = parse_aca_table(html)

    # 2) Build competitor badges from ACI excel (if target provided and file exists)
    badges = {}
    excel_exists = Path(aci_excel_path).exists()
    if target_iata and excel_exists:
        try:
            badges = _build_badges_from_aci(aci_excel_path, target_iata)
        except Exception:
            badges = {}

    payload = make_payload(aca_df, badges, target_iata)

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data_json = json.dumps(payload, separators=(",", ":"))

    # Determine default region
    if target_iata and (aca_df["iata"] == target_iata).any():
        default_region = aca_df.loc[aca_df["iata"] == target_iata, "region4"].iloc[0]
    else:
        default_region = "Americas" if "Americas" in payload["regions"] else (payload["regions"][0] if payload["regions"] else "")

    page = f"""<!doctype html>
<meta charset="utf-8">
<title>ACA Airports — Region Table</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<meta http-equiv="Expires" content="0"/>
<style>
  :root {{ --card-bg:#fff; --ink:#39424e; --muted:#6b7785; --border:#e6e8ec; }}
  body {{ margin:0; padding:20px; font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; color:var(--ink); background:#f6f8fb; }}
  .wrap {{ max-width:1100px; margin:0 auto; }}
  .card {{ background:var(--card-bg); border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,.08); padding:18px 18px; }}
  h1 {{ margin:0 0 10px 0; font-size:20px; }}
  .row {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
  .muted {{ color:var(--muted); font-size:13px; }}
  select {{ font:14px/1.2 inherit; padding:6px 10px; border-radius:8px; border:1px solid var(--border); background:#fff; }}

  table {{ width:100%; border-collapse:separate; border-spacing:0; margin-top:12px; font-size:14px; }}
  thead th {{ text-align:left; font-weight:600; padding:8px 10px; border-bottom:1px solid var(--border); background:#fafbfc; position:sticky; top:0; }}
  tbody td {{ padding:8px 10px; border-bottom:1px solid var(--border); vertical-align:top; }}
  td.lvl {{ font-weight:700; width:110px; white-space:nowrap; }}
  td.count {{ text-align:right; width:80px; color:var(--muted); }}
  td.codes code {{
    display:inline-flex; align-items:center; gap:6px;
    font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    font-size:13px; background:#f5f7fb; padding:4px 8px; border-radius:6px; margin:3px 6px 3px 0;
    position:relative;
  }}
  code.hl {{ outline:2px solid #E74C3C; outline-offset:1px; }}

  /* Badge row inside code chip */
  .badges {{ display:inline-flex; gap:6px; align-items:center; margin-left:4px; flex-wrap:wrap; }}
  .badge {{ display:inline-flex; align-items:center; gap:3px; padding:2px 6px; border-radius:999px; font-size:12px; font-weight:600; }}
  .b-pax    {{ background:#e8f1ff; color:#0b5ed7; border:1px solid #b9d3ff; }}
  .b-growth {{ background:#e7f6ef; color:#1e7a3b; border:1px solid #b8e4cc; }}
  .b-share  {{ background:#efe7f6; color:#6c2dbb; border:1px solid #dac6f0; }}
  .b-target {{ background:#fdeaea; color:#b91c1c; border:1px solid #f8c8c8; }}

  /* tiny inline SVG icons */
  .ico {{ width:12px; height:12px; display:inline-block; }}
</style>

<div class="wrap">
  <div class="card">
    <div class="row">
      <h1>ACA Airports by Region</h1>
      <div class="muted">Last updated: {updated}</div>
    </div>
    <div class="row" style="margin-top:6px;">
      <label for="regionSelect" class="muted">Region:</label>
      <select id="regionSelect" aria-label="Choose region"></select>
      <div class="muted" style="margin-left:auto">
        <span class="badge b-pax"><span class="ico" aria-hidden="true">{_svg_person()}</span> Passengers</span>
        <span class="badge b-growth"><span class="ico" aria-hidden="true">{_svg_arrow_ne()}</span> Growth</span>
        <span class="badge b-share"><span class="ico" aria-hidden="true">{_svg_pie()}</span> Share</span>
        <span class="badge b-target"><span class="ico" aria-hidden="true">{_svg_star()}</span> Target</span>
      </div>
    </div>

    <table id="acaTable" aria-live="polite">
      <thead>
        <tr>
          <th>ACA Level</th>
          <th>Airport Codes</th>
          <th style="text-align:right">Count</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>

    <div class="muted" style="margin-top:8px">Codes are IATA; levels sorted 5 → 1. Badges show top-5 competitors vs target airport.</div>
  </div>
</div>

<script id="aca-data" type="application/json">{data_json}</script>
<script>
(function(){{
  const DATA = JSON.parse(document.getElementById('aca-data').textContent);
  const sel = document.getElementById('regionSelect');
  const tbody = document.querySelector('#acaTable tbody');
  const levels = DATA.levels_desc || [];
  const regions = DATA.regions || [];
  const byRegion = DATA.by_region || {{}};
  const badges = DATA.badges || {{}};
  const target = DATA.target || "";
  const defaultRegion = "{default_region}";

  function option(v,t){{const o=document.createElement('option');o.value=v;o.textContent=t;return o;}}
  regions.forEach(r=>sel.appendChild(option(r,r)));
  if (regions.includes(defaultRegion)) sel.value = defaultRegion;

  // tiny SVGs inline (match legend)
  function svg_star()  {{ return `{_svg_star()}`;  }}
  function svg_arrow() {{ return `{_svg_arrow_ne()}`; }}
  function svg_pie()   {{ return `{_svg_pie()}`;   }}
  function svg_person(){{ return `{_svg_person()}`; }}

  function render(region){{
    tbody.innerHTML='';
    let total=0;
    const buckets = byRegion[region] || {{}};
    levels.forEach(lvl=>{{
      const codes = (buckets[lvl] || []).slice().sort();
      total += codes.length;
      const tr = document.createElement('tr');
      const tdLvl=document.createElement('td'); tdLvl.className='lvl'; tdLvl.textContent=lvl;
      const tdCodes=document.createElement('td'); tdCodes.className='codes';
      const tdCount=document.createElement('td'); tdCount.className='count'; tdCount.textContent=String(codes.length);
      if(codes.length){{
        codes.forEach(c=>{{
          const chip=document.createElement('code'); chip.textContent=c;
          if (c===target) chip.classList.add('hl');

          // badges row
          const list = badges[c] || [];
          if (c===target && !list.some(b=>b.k==='target')) list.unshift({{k:'target', t:''}});
          if (list.length){{
            const bs=document.createElement('span'); bs.className='badges';
            list.forEach(b=>{{
              const span=document.createElement('span');
              span.className='badge b-'+b.k;
              const ico=document.createElement('span'); ico.className='ico'; 
              if (b.k==='pax')   ico.innerHTML = svg_person();
              else if (b.k==='growth') ico.innerHTML = svg_arrow();
              else if (b.k==='share')  ico.innerHTML = svg_pie();
              else if (b.k==='target') ico.innerHTML = svg_star();
              span.appendChild(ico);
              if (b.k!=='target') {{
                const t=document.createTextNode(' '+(b.t||'')); 
                span.appendChild(t);
              }}
              bs.appendChild(span);
            }});
            chip.appendChild(bs);
          }}
          tdCodes.appendChild(chip);
        }});
      }} else {{
        tdCodes.innerHTML='<span class="muted">—</span>';
      }}
      tr.appendChild(tdLvl); tr.appendChild(tdCodes); tr.appendChild(tdCount);
      tbody.appendChild(tr);
    }});
    const trTotal=document.createElement('tr');
    trTotal.innerHTML='<td class="lvl">Total</td><td></td><td class="count">'+total+'</td>';
    tbody.appendChild(trTotal);
  }}

  sel.addEventListener('change', ()=>render(sel.value));
  render(sel.value || regions[0] || '');
}})();
</script>
"""
    return page, aca_df

# --- Tiny SVG helpers (inline, minimalist shapes) ---
def _svg_star():
    return """<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 17.3l-6.16 3.24 1.18-6.88L2 8.9l6.92-1 3.08-6.25 3.08 6.25 6.92 1-5.02 4.76 1.18 6.88z"/></svg>"""

def _svg_arrow_ne():
    return """<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 17l8-8v5h2V5H10v2h5l-8 8z"/></svg>"""

def _svg_pie():
    return """<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 2v9H2a10 10 0 0010 10 10 10 0 000-20z"/><path d="M13 2a10 10 0 019.95 9H13V2z"/></svg>"""

def _svg_person():
    return """<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="7" r="4"/><path d="M4 20a8 8 0 0116 0H4z"/></svg>"""
