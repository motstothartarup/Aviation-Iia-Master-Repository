# scripts/build_grid.py
# Output: Throughput-only list and grid from your ACI Excel.
# For a given target IATA, finds the 15 closest airports by total passengers.
# Exposes build_grid(...). Also runnable as a script to write docs/grid.html.

import os, re, argparse
import numpy as np  # kept, even if lightly used, for compatibility
import pandas as pd

CSS = """
<style>
:root{
  --gap:10px; --radius:14px; --ink:#111827; --muted:#6b7280; --border:#e5e7eb; --chipbg:#f6f8fa;
}
.container{max-width:820px;margin:14px auto 18px auto;padding:0 10px;font-family:Inter,system-ui,Arial}
.header h3{margin:0 0 4px 0}
.header .meta{color:var(--muted)}
.row{display:grid;grid-template-columns:240px 1fr;column-gap:16px;align-items:start;margin:10px 0}
.cat{font-weight:800;line-height:1.2}
.cat .sub{display:block;color:var(--muted);font-weight:500;font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(5,minmax(84px,1fr));gap:var(--gap)} /* 5 columns -> 3 rows for 15 chips */
.chip{
  display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:64px;
  padding:8px 10px;border:1px solid #d1d5db;border-radius:var(--radius);background:var(--chipbg);
  color:var(--ink);text-align:center;position:relative;
}
.chip .code{font-weight:800;line-height:1.05}
.chip .pax{font-size:11px;color:var(--ink);line-height:1.05;margin-top:2px}
.chip .dev{font-size:11px;color:var(--muted);line-height:1.05;margin-top:1px}
.chip.origin{box-shadow:0 0 0 2px rgba(231,76,60,.22) inset;border-color:#E74C3C}
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

    return df

def _dev(val, target):
    """
    Percentage deviation vs target, as a percent of target.
    """
    if pd.isna(val) or pd.isna(target):
        return ""
    if abs(target) < 1e-9:
        return ""
    diff_pct = (float(val) - float(target)) / float(target) * 100.0
    return _fmt_pct(diff_pct, signed=True, decimals=1)

def _grid_html(rows, metric_col, target_val, origin_iata):
    chips = []
    for _, r in rows.iterrows():
        code = str(r["iata"])
        pax_val = r[metric_col]
        pax_txt = _fmt_int(pax_val)
        dev_txt = _dev(pax_val, target_val)

        pax_html = f"<span class='pax'>{pax_txt} passengers</span>"
        dev_html = f"<span class='dev'>{dev_txt} vs target</span>" if dev_txt else "<span class='dev'>&nbsp;</span>"

        cls = "chip origin" if code == origin_iata else "chip"
        chips.append(
            f"<div class='{cls}'>"
            f"<span class='code'>{code}</span>"
            f"{pax_html}"
            f"{dev_html}"
            f"</div>"
        )
    return "".join(chips)  # exactly top-N (no fillers)

def _nearest_sets(df, iata, topn=15):
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
    union = {iata} | set(r_total["iata"])
    return t, sets, union

def build_grid(excel_path: str,
               iata: str,
               wsize: float,
               wgrowth_unused: float,
               out_html: str | None = None):
    """
    Build a throughput-only similarity set for a target IATA.

    Arguments kept compatible:
      - wsize and wgrowth_unused are accepted but no longer affect the selection.
    """
    if wsize > 100:
        raise ValueError("wsize must be <= 100")

    df = _load_aci(excel_path)
    if df[df["iata"] == iata].empty:
        raise ValueError(f"IATA '{iata}' not found in ACI file.")

    target_iata = iata.upper()
    target, sets, union = _nearest_sets(df, target_iata, topn=15)
    r_total = sets["total"]

    # Build grid (15 chips) for total passengers
    target_total = df.loc[df["iata"] == target_iata, "total_passengers"].iloc[0]
    total_html = _grid_html(r_total, "total_passengers", target_total, target_iata)

    # Reference values for TARGET airport
    ref_total = f"{target_iata}: {_fmt_int(target_total)} passengers"

    # Titles and labels
    doc_title = f"{target_iata} \u2013 Insights on passenger throughput versus ACA scoring"
    header_title = doc_title
    cat_label = f"Airports with similar passenger throughput to {target_iata}"

    header = f"""
    <div class="header">
      <h3>{header_title}</h3>
      <div class="meta">Total passengers at {target_iata}: {_fmt_int(target_total)}</div>
    </div>"""

    html = f"""<!doctype html><meta charset="utf-8">
<title>{doc_title}</title>
{CSS}
<div class="container">
  {header}

  <div class="row">
    <div class="cat">{cat_label}<span class="sub">Target \u2013 {ref_total}</span></div>
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
