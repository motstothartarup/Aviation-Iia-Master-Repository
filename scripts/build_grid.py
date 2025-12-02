# scripts/build_grid.py
# Output: Competitor Grid (throughput-only) from your ACI Excel.
# For a given target IATA, finds the closest airports by total passengers.
# Exposes build_grid(...). Also runnable as a script to write docs/grid.html.

import os, re, argparse
import numpy as np
import pandas as pd

# Region colors are left in place for chip styling, but we no longer
# compute or use any region buckets in the logic itself.
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

def _norm(s): 
    return re.sub(r"\s+"," ",str(s)).strip().lower()

def _pick(df, cands):
    for c in cands:
        if c in df.columns:
            return c
    return None

def _fmt_int(n):
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return "-"

def _fmt_pct(x, signed=False, decimals=1):
    if pd.isna(x):
        return "-"
    val = float(x)
    sign = "+" if (signed and val >= 0) else ""
    return f"{sign}{val:.{decimals}f}%"

def _load_aci(excel_path: str) -> pd.DataFrame:
    """
    Load ACI Excel and return a DataFrame with:
      - iata
      - name
      - state
      - total_passengers
      - region4 (now always 'Unknown' – no region mapping logic)
    """
    raw = pd.read_excel(excel_path, header=2)
    df = raw.rename(columns={c: _norm(c) for c in raw.columns}).copy()

    c_country   = _pick(df, ["country"])
    c_citystate = _pick(df, ["city/state", "citystate", "city, state", "city / state"])
    c_airport   = _pick(df, ["airport name", "airport"])
    c_iata      = _pick(df, ["airport code", "iata", "code"])
    c_total     = _pick(df, ["total passengers", "passengers total", "total pax"])
    # growth column not used anymore; we still try to find it but we won't rely on it
    c_yoy       = _pick(df, ["% chg 2024-2023", "% chg 2024 - 2023",
                             "% chg 2023-2022", "yoy %", "% change"])

    if c_country:
        df = df[df[c_country].astype(str).str.contains("United States", case=False, na=False)]

    def _state(s):
        if not isinstance(s, str):
            return None
        parts = re.split(r"\s+", s.strip())
        return parts[-1] if parts else None

    df["state"] = df[c_citystate].apply(_state) if c_citystate else None
    df["name"]  = df[c_airport].astype(str)
    df["iata"]  = df[c_iata].astype(str).str.upper()
    df["total_passengers"] = pd.to_numeric(df[c_total], errors="coerce")
    df["yoy_growth_pct"]   = pd.to_numeric(df[c_yoy], errors="coerce") if c_yoy else np.nan

    # Keep only rows with usable identifiers and throughput
    df = df.dropna(subset=["iata", "state", "total_passengers"]).reset_index(drop=True)

    # We no longer bucket airports into regions for this task.
    df["region4"] = "Unknown"

    return df

def _dev(val, target, pct):
    """
    Simple deviation display helper.
    For throughput, pct=False, so this is relative % difference vs target.
    """
    if pd.isna(val) or pd.isna(target):
        return ""
    diff = float(val) - float(target)
    if pct:
        if abs(target) < 1e-9:
            return f"{diff:+.1f}pp"
        return f"{(diff / target) * 100:+.1f}%"
    if abs(target) < 1e-9:
        return ""
    return f"{(diff / target) * 100:+.1f}%"

def _chip_color_style(region: str) -> str:
    color = REGION_COLORS.get(region, REGION_COLORS["Unknown"])
    return f"border-color:{color};"

def _grid_html(rows, metric_col, target_val, pct_metric, origin_iata):
    chips = []
    for _, r in rows.iterrows():
        code = str(r["iata"])
        dev  = _dev(r[metric_col], target_val, pct_metric)
        dev_html = f"<span class='dev'>{dev}</span>" if dev else "<span class='dev'>&nbsp;</span>"
        cls = "chip origin" if code == origin_iata else "chip"
        region = r.get("region4", "Unknown")
        dot_color = REGION_COLORS.get(region, REGION_COLORS["Unknown"])
        style = _chip_color_style(region)
        chips.append(
            f"<div class='{cls}' data-region='{region}' style='{style}'>"
            f"<span class='dot' style='background:{dot_color}' aria-hidden='true'></span>"
            f"<span class='code'>{code}</span>{dev_html}</div>"
        )
    return "".join(chips)  # exactly top-N (no fillers)

def _nearest_sets(df, iata, w_size, w_share, topn=7):
    """
    Throughput-only similarity.
    Finds the top-N airports with total passengers closest to the target.
    """
    t = df.loc[df["iata"] == iata].iloc[0]
    cand = df[df["iata"] != iata].copy()

    # TOTAL: closest by absolute throughput difference (unique IATA, stable ties)
    cand = cand.assign(abs_diff_pax=(cand["total_passengers"] - t["total_passengers"]).abs())
    r_total = (
        cand.sort_values(["abs_diff_pax", "total_passengers"], ascending=[True, False])
            .drop_duplicates(subset=["iata"], keep="first")
            .head(topn)
    )

    sets = {"total": r_total}
    # Keep union tied to TOTAL only
    union = {iata} | set(r_total["iata"])
    return t, sets, union

def build_grid(excel_path: str,
               iata: str,
               wsize: float,
               wgrowth_unused: float,
               out_html: str | None = None):
    """
    Build a throughput-only competitor set for a target IATA.

    Arguments kept compatible:
      - wsize and wgrowth_unused are accepted but no longer affect the selection.
    """
    # Keep argument validation light; wsize is no longer functionally used.
    if wsize > 100:
        raise ValueError("wsize must be <= 100")

    df = _load_aci(excel_path)
    if df[df["iata"] == iata].empty:
        raise ValueError(f"IATA '{iata}' not found in ACI file.")

    # wshare kept for signature compatibility, but not used in logic now
    wshare = 100 - wsize

    target, sets, union = _nearest_sets(df, iata, wsize, wshare, topn=7)
    r_total = sets["total"]

    # Build grid (7 chips) for total passengers
    target_total = df.loc[df["iata"] == iata, "total_passengers"].iloc[0]
    total_html = _grid_html(r_total, "total_passengers", target_total, False, iata)

    # Reference values for TARGET airport
    ref_total = f"{target['iata']}: {_fmt_int(target['total_passengers'])}"

    header = f"""
    <div class="header">
      <h3>{target['iata']} — {target['name']}</h3>
      <div class="meta">State: {target['state']} · Pax (total): {_fmt_int(target['total_passengers'])}</div>
    </div>"""

    html = f"""<!doctype html><meta charset="utf-8"><title>Competitor Grid</title>
{CSS}
<div class="container">
  {header}

  <div class="row">
    <div class="cat">Total passengers (int’l + dom)<span class="sub">Target — {ref_total}</span></div>
    <div class="grid">{total_html}</div>
  </div>
</div>"""

    if out_html:
        os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)

    # Pure throughput-based nearest list (excluding the target itself)
    nearest_list = r_total["iata"].tolist()

    return {
        "html": html,
        "union": sorted(list(union)),
        "nearest": nearest_list,
        "target": dict(target),
        # Weights kept for backward-compat reporting; logically size/share is now moot.
        "weights": (float(wsize), float(100 - wsize)),
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default="data/ACI_2024_NA_Traffic.xlsx")
    ap.add_argument("--iata", required=True)
    ap.add_argument(
        "--wsize",
        type=float,
        required=True,
        help="Weight for size (0-100). Kept for backward-compat; logic is throughput-only.",
    )
    ap.add_argument(
        "--wgrowth",
        type=float,
        required=False,
        default=0.0,
        help="Ignored. Present for backward-compat.",
    )
    ap.add_argument("--out", default="docs/grid.html")
    a = ap.parse_args()

    res = build_grid(a.excel, a.iata.upper(), a.wsize, a.wgrowth, a.out)

    # Primary output: list of nearest airports by throughput
    print("Nearest airports by throughput (excluding target):")
    print(", ".join(res["nearest"]))

    if a.out:
        print("Wrote", a.out)
