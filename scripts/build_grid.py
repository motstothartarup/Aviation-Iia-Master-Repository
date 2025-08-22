# scripts/build_grid.py
# Output 1: Competitor Grid (5×4) from your ACI Excel.
# Exposes build_grid(...). Also runnable as a script to write docs/grid.html.

import os, re, argparse
import numpy as np
import pandas as pd

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

# ==== EDIT THESE to exactly match your ACA map palette (by FAA Region) ====
REGION_COLORS = {
    "Alaskan":            "#2E7D32",
    "New England":        "#1957A6",
    "Eastern":            "#7E57C2",
    "Southern":           "#E83F2E",
    "Great Lakes":        "#00838F",
    "Central":            "#6D6E71",
    "Southwest":          "#F59E0B",
    "Northwest Mountain": "#10B981",
    "Western-Pacific":    "#EF4444",
    "Unknown":            "#9aa2af",
}
# ========================================================================

CSS = """
<style>
:root{
  --gap:10px; --radius:14px; --ink:#111827; --muted:#6b7280; --border:#e5e7eb; --chipbg:#f6f8fa;
}
.container{max-width:1100px;margin:14px auto 18px auto;padding:0 10px;font-family:Inter,system-ui,Arial}
.header h3{margin:0 0 4px 0}
.header .meta{color:var(--muted)}
.row{display:grid;grid-template-columns:240px 1fr;column-gap:16px;align-items:start;margin:10px 0}
.cat{font-weight:800;line-height:1.2}
.cat .sub{display:block;color:var(--muted);font-weight:500;font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(5,minmax(84px,1fr));gap:var(--gap)} /* 5 columns */
.chip{
  display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:56px;
  padding:8px 10px;border:1px solid #9aa2af;border-radius:var(--radius);background:var(--chipbg);
  color:var(--ink);text-align:center; position:relative;
}
.chip .code{font-weight:800;line-height:1.05}
.chip .dev{font-size:11px;color:var(--muted);line-height:1.05;margin-top:2px}
.chip.origin{box-shadow:0 0 0 2px rgba(231,76,60,.22) inset;border-color:#E74C3C}
.dot{ width:10px;height:10px;border-radius:999px;position:absolute;top:8px;left:8px; }
</style>
"""

def _norm(s): return re.sub(r"\s+"," ",str(s)).strip().lower()
def _pick(df, cands):
    for c in cands:
        if c in df.columns: return c

def _fmt_int(n):
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return "-"

def _fmt_pct(x, signed=False, decimals=1):
    if pd.isna(x): return "-"
    val = float(x)
    sign = "+" if (signed and val>=0) else ""
    return f"{sign}{val:.{decimals}f}%"

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

def _chip_color_style(region: str) -> str:
    color = REGION_COLORS.get(region, REGION_COLORS["Unknown"])
    return f"border-color:{color};"

def _grid_html(rows, metric_col, target_val, pct_metric, origin_iata):
    chips=[]
    for _, r in rows.iterrows():
        code=str(r["iata"])
        dev=_dev(r[metric_col], target_val, pct_metric)
        dev_html=f"<span class='dev'>{dev}</span>" if dev else "<span class='dev'>&nbsp;</span>"
        cls="chip origin" if code==origin_iata else "chip"
        dot_color = REGION_COLORS.get(r.get("faa_region","Unknown"), REGION_COLORS["Unknown"])
        style = _chip_color_style(r.get("faa_region","Unknown"))
        chips.append(
            f"<div class='{cls}' data-region='{r.get('faa_region','Unknown')}' style='{style}'>"
            f"<span class='dot' style='background:{dot_color}' aria-hidden='true'></span>"
            f"<span class='code'>{code}</span>{dev_html}</div>"
        )
    return "".join(chips)  # no fillers; exactly top 5

def _nearest_sets(df, iata, w_size, w_growth, w_share, topn=5):
    t = df.loc[df["iata"]==iata].iloc[0]
    # total
    cand = df[df["iata"]!=iata].copy()
    r1 = cand.assign(abs_diff_pax=(cand["total_passengers"]-t["total_passengers"]).abs()) \
             .sort_values("abs_diff_pax").head(topn)
    # growth (median fallback)
    g = pd.to_numeric(cand["yoy_growth_pct"], errors="coerce")
    g_med = g.median()
    tg = t["yoy_growth_pct"] if pd.notna(t["yoy_growth_pct"]) else g_med
    r2 = cand.assign(yoy_growth_pct=g.fillna(g_med),
                     abs_diff_growth=(g.fillna(g_med)-tg).abs(),
                     _target_growth=tg).sort_values("abs_diff_growth").head(topn)
    # share any-region
    r3 = cand.assign(abs_diff_share=(cand["share_of_region_pct"]-t["share_of_region_pct"]).abs()) \
             .sort_values("abs_diff_share").head(topn)
    # composite
    s = max(1e-9, w_size+w_growth+w_share)
    w_size, w_growth, w_share = w_size/s, w_growth/s, w_share/s
    size_sim = 1 - ((np.log1p(cand["total_passengers"]) - np.log1p(t["total_passengers"])).abs()
                    /(np.log1p(cand["total_passengers"]).abs().max()+1e-9))
    gg = g.fillna(g_med); growth_sim = 1 - ((gg - tg).abs()/(gg.abs().max()+1e-9))
    diff = (cand["share_of_region_pct"]-t["share_of_region_pct"]).abs()
    share_sim = 1 - (diff/(diff.max()+1e-9))
    r4 = cand.assign(score=(w_size*size_sim + w_growth*growth_sim + w_share*share_sim)) \
             .sort_values("score", ascending=False).head(topn)
    sets = {"total": r1, "growth": r2, "share": r3, "composite": r4}
    union = {iata} | set(r1["iata"]) | set(r2["iata"]) | set(r3["iata"]) | set(r4["iata"])
    return t, sets, union

def build_grid(excel_path: str, iata: str, wsize: float, wgrowth: float, out_html: str | None = None):
    if wsize + wgrowth > 100: raise ValueError("wsize + wgrowth must be <= 100")
    wshare = 100 - (wsize + wgrowth)
    df = _load_aci(excel_path)
    if df[df["iata"]==iata].empty:
        raise ValueError(f"IATA '{iata}' not found in ACI file.")
    target, sets, union = _nearest_sets(df, iata, wsize, wgrowth, wshare, topn=5)
    r1, r2, r3, r4 = sets["total"], sets["growth"], sets["share"], sets["composite"]
    growth_target = r2["_target_growth"].iloc[0] if "_target_growth" in r2.columns else target["yoy_growth_pct"]

    # Build grids
    total  = _grid_html(r1, "total_passengers",      target["total_passengers"],      False, iata)
    growth = _grid_html(r2, "yoy_growth_pct",        growth_target,                   True,  iata)
    share  = _grid_html(r3, "share_of_region_pct",   target["share_of_region_pct"],   True,  iata)
    comp   = _grid_html(r4, "total_passengers",      target["total_passengers"],      False, iata)

    # Reference values to show under each header for the TARGET airport
    ref_total = f"{target['iata']}: {_fmt_int(target['total_passengers'])}"
    ref_growth = f"{target['iata']}: {_fmt_pct(growth_target, signed=True)}"
    ref_share = f"{target['iata']}: {_fmt_pct(target['share_of_region_pct'], signed=False, decimals=2)}"

    header = f"""
    <div class="header">
      <h3>{target['iata']} — {target['name']}</h3>
      <div class="meta">State: {target['state']} · FAA: {target['faa_region']} ·
      Pax (total): {_fmt_int(target['total_passengers'])} · Share of region: {_fmt_pct(target['share_of_region_pct'], decimals=2)}</div>
    </div>"""

    html = f"""<!doctype html><meta charset="utf-8"><title>Competitor Grid</title>
{CSS}
<div class="container">
  {header}

  <div class="row">
    <div class="cat">Total passengers (int’l + dom)<span class="sub">Target — {ref_total}</span></div>
    <div class="grid">{total}</div>
  </div>

  <div class="row">
    <div class="cat">Growth 2023→2024<span class="sub">Target — {ref_growth}</span></div>
    <div class="grid">{growth}</div>
  </div>

  <div class="row">
    <div class="cat">Share of region (airport ÷ FAA region)<span class="sub">Target — {ref_share}</span></div>
    <div class="grid">{share}</div>
  </div>

  <div class="row">
    <div class="cat">Composite (weights: {wsize:.0f}/{wgrowth:.0f}/{wshare:.0f})<span class="sub">Top 5 closest overall</span></div>
    <div class="grid">{comp}</div>
  </div>
</div>"""

    if out_html:
        os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)

    return {
        "html": html,
        "union": sorted(list(union)),
        "target": dict(target),
        "weights": (float(wsize), float(wgrowth), float(wshare)),
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default="data/ACI_2024_NA_Traffic.xlsx")
    ap.add_argument("--iata", required=True)
    ap.add_argument("--wsize", type=float, required=True)
    ap.add_argument("--wgrowth", type=float, required=True)
    ap.add_argument("--out", default="docs/grid.html")
    a = ap.parse_args()
    res = build_grid(a.excel, a.iata.upper(), a.wsize, a.wgrowth, a.out)
    print("Wrote", a.out)
