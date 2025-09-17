# scripts/build_grid.py
# Output: Competitor Grid (7×3) from your ACI Excel.
# Rows: Total (7), Share of Region (7), Composite (size+share, 7)
# Exposes build_grid(...). Also runnable as a script to write docs/grid.html.

import os, re, argparse
import numpy as np
import pandas as pd

# ==== Simplified 4-Region Map (lower granularity, fewer colors) ====
REGIONS_4 = {
    "West":     {"WA","OR","CA","NV","ID","MT","WY","UT","AZ","CO","NM","AK","HI"},
    "Midwest":  {"ND","SD","NE","KS","MN","IA","MO","WI","IL","MI","IN","OH"},
    "South":    {"OK","TX","AR","LA","KY","TN","MS","AL","GA","FL","SC","NC","VA","WV","MD","DC","DE"},
    "Northeast":{"PA","NJ","NY","CT","RI","MA","VT","NH","ME"},
}

REGION_COLORS = {
    "West":      "#1957A6",
    "Midwest":   "#10B981",
    "South":     "#F59E0B",
    "Northeast": "#7E57C2",
    "Unknown":   "#9aa2af",
}
# ===================================================================

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
.grid{display:grid;grid-template-columns:repeat(7,minmax(84px,1fr));gap:var(--gap)} /* 7 columns */
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
    # growth column not used anymore; we still try to find it but we won't rely on it
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

    def _region4(st):
        s = str(st).upper()
        for reg, states in REGIONS_4.items():
            if s in states: return reg
        return "Unknown"

    df["region4"] = df["state"].apply(_region4)
    region_totals = df.groupby("region4")["total_passengers"].sum().rename("region_total")
    df = df.merge(region_totals, on="region4", how="left")
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
        dot_color = REGION_COLORS.get(r.get("region4","Unknown"), REGION_COLORS["Unknown"])
        style = _chip_color_style(r.get("region4","Unknown"))
        chips.append(
            f"<div class='{cls}' data-region='{r.get('region4','Unknown')}' style='{style}'>"
            f"<span class='dot' style='background:{dot_color}' aria-hidden='true'></span>"
            f"<span class='code'>{code}</span>{dev_html}</div>"
        )
    return "".join(chips)  # exactly top-N (no fillers)

def _nearest_sets(df, iata, w_size, w_share, topn=7):
    t = df.loc[df["iata"]==iata].iloc[0]
    cand = df[df["iata"] != iata].copy()

    # TOTAL: closest by absolute throughput difference (unique IATA, stable ties)
    cand = cand.assign(abs_diff_pax=(cand["total_passengers"] - t["total_passengers"]).abs())
    r_total = (
        cand.sort_values(["abs_diff_pax", "total_passengers"], ascending=[True, False])
            .drop_duplicates(subset=["iata"], keep="first")
            .head(topn)
    )

    # SHARE: closest by absolute share-of-region difference (unique IATA)
    cand = cand.assign(abs_diff_share=(cand["share_of_region_pct"] - t["share_of_region_pct"]).abs())
    r_share = (
        cand.sort_values(["abs_diff_share", "total_passengers"], ascending=[True, False])
            .drop_duplicates(subset=["iata"], keep="first")
            .head(topn)
    )

    # COMPOSITE: weighted similarity of size & share ONLY (no growth)
    s = max(1e-9, w_size + w_share)
    w_size_n, w_share_n = w_size/s, w_share/s

    # Normalize similarities to [0,1] ranges
    size_sim = 1 - (
        (np.log1p(cand["total_passengers"]) - np.log1p(t["total_passengers"])).abs()
        / (np.log1p(cand["total_passengers"]).abs().max() + 1e-9)
    )
    diff_share = (cand["share_of_region_pct"] - t["share_of_region_pct"]).abs()
    share_sim  = 1 - (diff_share / (diff_share.max() + 1e-9))

    r_comp = (
        cand.assign(score=(w_size_n*size_sim + w_share_n*share_sim))
            .sort_values("score", ascending=False)
            .drop_duplicates(subset=["iata"], keep="first")
            .head(topn)
    )

    sets = {"total": r_total, "share": r_share, "composite": r_comp}
    # Keep map 'union' tied to TOTAL only (unchanged behavior). If you want total∪share, swap this line.
    union = {iata} | set(r_total["iata"])
    return t, sets, union

def build_grid(excel_path: str, iata: str, wsize: float, wgrowth_unused: float, out_html: str | None = None):
    # Interpret weights as: wsize (size), wgrowth_unused ignored, wshare derived
    if wsize > 100: raise ValueError("wsize must be <= 100")
    wshare = 100 - wsize

    df = _load_aci(excel_path)
    if df[df["iata"]==iata].empty:
        raise ValueError(f"IATA '{iata}' not found in ACI file.")

    target, sets, union = _nearest_sets(df, iata, wsize, wshare, topn=7)
    r_total, r_share, r_comp = sets["total"], sets["share"], sets["composite"]

    # Build grids (7 chips each)
    total_html = _grid_html(r_total, "total_passengers",    df.loc[df["iata"]==iata,"total_passengers"].iloc[0], False, iata)
    share_html = _grid_html(r_share,  "share_of_region_pct",df.loc[df["iata"]==iata,"share_of_region_pct"].iloc[0], True,  iata)
    comp_html  = _grid_html(r_comp,   "total_passengers",    df.loc[df["iata"]==iata,"total_passengers"].iloc[0], False, iata)

    # Reference values for TARGET airport
    ref_total = f"{target['iata']}: {_fmt_int(target['total_passengers'])}"
    ref_share = f"{target['iata']}: {_fmt_pct(target['share_of_region_pct'], signed=False, decimals=2)}"

    header = f"""
    <div class="header">
      <h3>{target['iata']} — {target['name']}</h3>
      <div class="meta">State: {target['state']} · Region: {target['region4']} ·
      Pax (total): {_fmt_int(target['total_passengers'])} · Share of region: {_fmt_pct(target['share_of_region_pct'], decimals=2)}</div>
    </div>"""

    html = f"""<!doctype html><meta charset="utf-8"><title>Competitor Grid</title>
{CSS}
<div class="container">
  {header}

  <div class="row">
    <div class="cat">Total passengers (int’l + dom)<span class="sub">Target — {ref_total}</span></div>
    <div class="grid">{total_html}</div>
  </div>

  <div class="row">
    <div class="cat">Share of region (airport ÷ 4-region bucket)<span class="sub">Target — {ref_share}</span></div>
    <div class="grid">{share_html}</div>
  </div>

  <div class="row">
    <div class="cat">Composite (size ⊕ share)<span class="sub">Weights: {wsize:.0f}/{(100-wsize):.0f}</span></div>
    <div class="grid">{comp_html}</div>
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
        # Report the effective weights used (size, share)
        "weights": (float(wsize), float(100 - wsize)),
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default="data/ACI_2024_NA_Traffic.xlsx")
    ap.add_argument("--iata", required=True)
    ap.add_argument("--wsize", type=float, required=True, help="Weight for size (0-100). Share weight = 100 - wsize.")
    ap.add_argument("--wgrowth", type=float, required=False, default=0.0, help="Ignored. Present for backward-compat.")
    ap.add_argument("--out", default="docs/grid.html")
    a = ap.parse_args()
    res = build_grid(a.excel, a.iata.upper(), a.wsize, a.wgrowth, a.out)
    print("Wrote", a.out)
