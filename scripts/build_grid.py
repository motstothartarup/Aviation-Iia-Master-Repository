# scripts/build_grid.py
# Output: Throughput-only list and grid from your ACI Excel.
# For a given target IATA, finds 10 closest airports within the target region group
# and 5 closest airports out of region, by total passengers.
# Exposes build_grid(...). Also runnable as a script to write docs/grid.html.

import os, re, argparse
import numpy as np  # kept in case you later extend logic
import pandas as pd

EXCEL_SHEET = "Working Global"
COUNTRY_REGION_MAP_CSV = os.path.join("data", "country_region_map.csv")

IN_REGION_N = 10
OUT_REGION_N = 5

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
.chip.oor{
  box-shadow:0 0 0 2px rgba(13,110,253,.25) inset;
  border-color:#0d6efd;
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
    Load ACI Excel from the 'Working Global' tab.

    Headers are on Excel row 3, so we read with header=2 (0-indexed).
    We only rely on fixed columns:
      - Rank: A
      - Country: C
      - Airport Code (IATA): F
      - Total Passengers: M
    """
    # Header row is Excel row 3 -> header=2
    df = pd.read_excel(
        excel_path,
        sheet_name=EXCEL_SHEET,
        header=2,
        usecols="A,C,F,M",
    ).copy()

    # Normalize and rename to stable internal names
    df.columns = ["rank", "country", "iata", "total_passengers"]

    df["country"] = df["country"].astype(str).str.strip()
    df["iata"] = df["iata"].astype(str).str.strip().str.upper()
    df["total_passengers"] = pd.to_numeric(df["total_passengers"], errors="coerce")

    # Basic cleanup
    df = df.dropna(subset=["iata", "country", "total_passengers"]).copy()
    df = df[df["iata"].astype(str).str.len().between(2, 4)].copy()
    df = df.drop_duplicates(subset=["iata"], keep="first").reset_index(drop=True)

    # Region grouping via mapping CSV
    if not os.path.exists(COUNTRY_REGION_MAP_CSV):
        raise RuntimeError(f"Missing country-to-region mapping file: {COUNTRY_REGION_MAP_CSV}")

    m = pd.read_csv(COUNTRY_REGION_MAP_CSV)
    m = m.rename(columns={c: _norm(c) for c in m.columns}).copy()

    c_m_country = _pick(m, ["country"])
    c_m_region  = _pick(m, ["region_group", "region", "group"])
    if not (c_m_country and c_m_region):
        raise RuntimeError(
            f"Mapping CSV must include columns: country, region_group. Found: {list(m.columns)}"
        )

    m = m[[c_m_country, c_m_region]].copy()
    m.columns = ["country", "region_group"]
    m["country"] = m["country"].astype(str).str.strip()
    m["region_group"] = m["region_group"].astype(str).str.strip()

    df = df.merge(m, on="country", how="left")
    df["region_group"] = df["region_group"].fillna("Unknown").astype(str).str.strip()

    return df

def _dev(val, target):
    """Percentage deviation vs target, as a percent of target."""
    if pd.isna(val) or pd.isna(target):
        return ""
    if abs(target) < 1e-9:
        return ""
    diff_pct = (float(val) - float(target)) / float(target) * 100.0
    return _fmt_pct(diff_pct, signed=True, decimals=1)

def _grid_html(rows, metric_col, target_val, origin_iata, out_of_region=None):
    chips = []
    oor = set(out_of_region or [])
    for _, r in rows.iterrows():
        code = str(r["iata"])
        pax_val = r[metric_col]
        pax_txt = _fmt_int(pax_val)
        dev_txt = _dev(pax_val, target_val)

        pax_html = f"<span class='pax'>{pax_txt} passengers</span>"
        dev_html = f"<span class='dev'>{dev_txt} vs target</span>" if dev_txt else "<span class='dev'>&nbsp;</span>"

        if code == origin_iata:
            cls = "chip origin"
        elif code in oor:
            cls = "chip oor"
        else:
            cls = "chip"

        chips.append(
            f"<div class='{cls}'>"
            f"<span class='code'>{code}</span>"
            f"{pax_html}"
            f"{dev_html}"
            f"</div>"
        )
    return "".join(chips)

def _nearest_sets(df, iata, in_n=IN_REGION_N, out_n=OUT_REGION_N):
    """
    Throughput-only similarity with region split:
      - in_n closest airports within the target's region_group
      - out_n closest airports outside the target's region_group
    Returns: target_row, peers_df, union_set, out_of_region_set
    """
    t = df.loc[df["iata"] == iata].iloc[0]
    cand = df[df["iata"] != iata].copy()

    target_region = str(t.get("region_group", "Unknown"))

    cand = cand.assign(abs_diff_pax=(cand["total_passengers"] - t["total_passengers"]).abs())

    cand_in = cand[cand["region_group"] == target_region].copy()
    cand_out = cand[cand["region_group"] != target_region].copy()

    top_in = (
        cand_in.sort_values(["abs_diff_pax", "total_passengers"], ascending=[True, False])
              .drop_duplicates(subset=["iata"], keep="first")
              .head(in_n)
    )
    top_out = (
        cand_out.sort_values(["abs_diff_pax", "total_passengers"], ascending=[True, False])
               .drop_duplicates(subset=["iata"], keep="first")
               .head(out_n)
    )

    peers = pd.concat([top_in, top_out], ignore_index=True)
    out_of_region = set(top_out["iata"].astype(str).str.upper().tolist())

    union = {iata} | set(peers["iata"].astype(str).str.upper().tolist())
    return t, peers, union, out_of_region

def build_grid(
    excel_path: str,
    iata: str,
    out_html: str | None = None,
):
    """
    Build a throughput-only similarity set for a target IATA.
    Selection rule:
      - 10 peers in the target's region_group
      - 5 peers out of region
    """
    df = _load_aci(excel_path)
    if df[df["iata"] == iata].empty:
        raise ValueError(f"IATA '{iata}' not found in ACI file.")

    target_iata = iata.upper()
    target, peers, union, out_of_region = _nearest_sets(df, target_iata)

    target_total = df.loc[df["iata"] == target_iata, "total_passengers"].iloc[0]
    total_html = _grid_html(
        peers, "total_passengers", target_total, target_iata, out_of_region=out_of_region
    )

    header_title = f"{target_iata} - overview of airports with similar throughput."
    header_meta  = f"Target: {target_iata} - {_fmt_int(target_total)} passengers"

    doc_title = f"{target_iata} - Airports with similar passenger throughput"

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

    nearest_list = peers["iata"].tolist()

    return {
        "html": html,
        "union": sorted(list(union)),
        "nearest": nearest_list,
        "target": dict(target),
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default="data/Copy of ACI 2024 North America Traffic Report (1).xlsx")
    ap.add_argument("--iata", required=True)
    ap.add_argument("--out", default="docs/grid.html")
    a = ap.parse_args()

    res = build_grid(a.excel, a.iata.upper(), out_html=a.out)

    print("Nearest airports by throughput (excluding target):")
    print(", ".join(res["nearest"]))

    if a.out:
        print("Wrote", a.out)
