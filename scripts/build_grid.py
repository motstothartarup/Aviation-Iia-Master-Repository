# scripts/build_grid.py
# Output: Throughput-only list and grid from your ACI Excel.
# For a given target IATA, finds the 15 closest airports by total passengers.
# Exposes build_grid(...). Also runnable as a script to write docs/grid.html.

import os, re, argparse
import numpy as np  # kept in case you later extend logic
import pandas as pd

CSS = """
<style>
:root{
  --gap:18px;
  --radius:16px;
  --ink:#111827;
  --muted:#6b7280;
  --border:#e5e7eb;
  --chipbg:#f6f8fa;
}
.container{
  max-width:100%;
  width:100%;
  margin:14px auto 18px auto;
  padding:0 24px 24px 24px;
  font-family:Inter,system-ui,Arial;
  box-sizing:border-box;
}
.header h3{
  margin:4px 0;
  font-size:20px;
}
.header .meta{
  color:var(--muted);
  margin-top:2px;
  font-size:13px;
}
.row{
  margin:18px 0 0 0;
}
.grid{
  display:grid;
  grid-template-columns:repeat(5,minmax(140px,1fr)); /* 5 columns, 3 rows for 15 tiles */
  gap:var(--gap);
}
.chip{
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  min-height:120px;
  padding:16px 18px;
  border:1px solid var(--border);
  border-radius:var(--radius);
  background:var(--chipbg);
  color:var(--ink);
  text-align:center;
  box-sizing:border-box;
}
.chip .code{
  font-weight:800;
  line-height:1.1;
  font-size:16px;
}
.chip .pax{
  font-size:13px;
  color:var(--ink);
  line-height:1.2;
  margin-top:6px;
}
.chip .dev{
  font-size:12px;
  color:var(--muted);
  line-height:1.2;
  margin-top:4px;
}
.chip.origin{
  box-shadow:0 0 0 2px rgba(231,76,60,.22) inset;
  border-color:#E74C3C;
}
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

    df = df.dropna(subset=["iata", "state", "total_passengers"]).reset_index(drop=True)

    return df

def _dev(val, target):
    """Percentage deviation vs target, as a percent of target."""
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
    return "".join(chips)

def _nearest_sets(df, iata, topn=15):
    """
    Throughput-only similarity.
    Finds the top-N airports with total passengers closest to the target.
    """
    t = df.loc[df["iata"] == iata].iloc[0]
    cand = df[df["iata"] != iata].copy()

    cand = cand.assign(abs_diff_pax=(cand["total_passengers"] - t["total_passengers"]).abs())
    r_total = (
        cand.sort_values(["abs_diff_pax", "total_passengers"], ascending=[True, False])
            .drop_duplicates(subset=["iata"], keep="first")
            .head(topn)
    )

    sets = {"total": r_total}
    union = {iata} | set(r_total["iata"])
    return t, sets, union

def build_grid(
    excel_path: str,
    iata: str,
    out_html: str | None = None,
):
    """
    Build a throughput-only similarity set for a target IATA.
    """
    df = _load_aci(excel_path)
    if df[df["iata"] == iata].empty:
        raise ValueError(f"IATA '{iata}' not found in ACI file.")

    target_iata = iata.upper()
    target, sets, union = _nearest_sets(df, target_iata, topn=15)
    r_total = sets["total"]

    target_total = df.loc[df["iata"] == target_iata, "total_passengers"].iloc[0]

    total_html = _grid_html(r_total, "total_passengers", target_total, target_iata)

    airport_name = str(target.get("name", target_iata))
    header_title = f"{airport_name} – overview of airports with similar throughput."
    header_meta  = f"Target: {target_iata} – {_fmt_int(target_total)} passengers"

    doc_title = f"{target_iata} – Airports with similar passenger throughput"

    header = f"""
    <div class="header">
      <h3>{header_title}</h3>
      <div class="meta">{header_meta}</div>
    </div>"""

    html = f"""<!doctype html><meta charset="utf-8">
<title>{doc_title}</title>
{CSS}
<div class="container">
  {header}
  <div class="row">
    <div class="grid">{total_html}</div>
  </div>
</div>"""

    if out_html:
        os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)

    nearest_list = r_total["iata"].tolist()

    return {
        "html": html,
        "union": sorted(list(union)),
        "nearest": nearest_list,
        "target": dict(target),
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default="data/ACI_2024_NA_Traffic.xlsx")
    ap.add_argument("--iata", required=True)
    ap.add_argument("--out", default="docs/grid.html")
    a = ap.parse_args()

    res = build_grid(a.excel, a.iata.upper(), out_html=a.out)

    print("Nearest airports by throughput (excluding target):")
    print(", ".join(res["nearest"]))

    if a.out:
        print("Wrote", a.out)
