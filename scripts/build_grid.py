# scripts/build_grid.py
# Output 1: Competitor Grid (10×4) from your ACI Excel.
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

CSS = """
<style>
.container{max-width:1100px;margin:18px auto;font-family:Inter,system-ui,Arial}
.header .meta{color:#6b7280}
.row{display:grid;grid-template-columns:190px 1fr;column-gap:16px;align-items:start;margin:12px 0}
.cat{font-weight:800}
.grid{display:grid;grid-template-columns:repeat(10,minmax(84px,1fr));gap:10px}
.chip{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:56px;
      padding:8px 10px;border:1px solid #9aa2af;border-radius:14px;background:#f6f8fa;color:#111827;text-align:center}
.chip .code{font-weight:800;line-height:1.05}
.chip .dev{font-size:11px;color:#6b7280;line-height:1.05;margin-top:2px}
.chip.empty{visibility:hidden}
.chip.origin{border-color:#E74C3C;box-shadow:0 0 0 2px rgba(231,76,60,.2) inset}
</style>
"""

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
    df["share_of_region_pct"] = (df["total_passengers"] / df["region_total"] * 100).round(3)
    return df

def _dev(val, target, pct):
    if pd.isna(val) or pd.isna(target): return ""
    diff = float(val) - float(target)
    if pct:
        if abs(target) < 1e-9: return f"{diff:+.1f}pp"
        return f"{(diff/target)*100:+.1f}%"
    if abs(target) < 1e-9: return ""
    return f"{(diff/target)*100:+.1f}%"

def _grid_html(rows, metric_col, target_val, pct_metric, origin_iata):
    chips=[]
    for _, r in rows.iterrows():
        code=str(r["iata"])
        dev=_dev(r[metric_col], target_val, pct_metric)
        dev_html=f"<span class='dev'>{dev}</span>" if dev else "<span class='dev'>&nbsp;</span>"
        cls="chip origin" if code==origin_iata else "chip"
        chips.append(f"<div class='{cls}'><span class='code'>{code}</span>{dev_html}</div>")
    while len(chips)<10:
        chips.append("<div class='chip empty'><span class='code'>&nbsp;</span><span class='dev'>&nbsp;</span></div>")
    return "".join(chips[:10])

def _nearest_sets(df, iata, w_size, w_growth, w_share, topn=10):
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
    target, sets, union = _nearest_sets(df, iata, wsize, wgrowth, wshare, 10)
    r1, r2, r3, r4 = sets["total"], sets["growth"], sets["share"], sets["composite"]
    growth_target = r2["_target_growth"].iloc[0] if "_target_growth" in r2.columns else target["yoy_growth_pct"]

    total  = _grid_html(r1, "total_passengers", target["total_passengers"], False, iata)
    growth = _grid_html(r2, "yoy_growth_pct",   growth_target, True,  iata)
    share  = _grid_html(r3, "share_of_region_pct", target["share_of_region_pct"], True, iata)
    comp   = _grid_html(r4, "total_passengers", target["total_passengers"], False, iata)  # composite: keep two-line UI

    header = f"""
    <div class="header">
      <h3 style="margin:0">{target['iata']} — {target['name']}</h3>
      <div class="meta">State: {target['state']} · FAA: {target['faa_region']} ·
      Pax: {int(target['total_passengers']):,} · Share: {target['share_of_region_pct']}%</div>
    </div>"""

    html = f"""<!doctype html><meta charset="utf-8"><title>Competitor Grid</title>
{CSS}
<div class="container">
  {header}
  <div class="row"><div class="cat">Total Passengers</div><div class="grid">{total}</div></div>
  <div class="row"><div class="cat">Growth (YoY %)</div><div class="grid">{growth}</div></div>
  <div class="row"><div class="cat">Share of Region</div><div class="grid">{share}</div></div>
  <div class="row"><div class="cat">Composite (weights: {wsize:.0f}/{wgrowth:.0f}/{wshare:.0f})</div><div class="grid">{comp}</div></div>
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
