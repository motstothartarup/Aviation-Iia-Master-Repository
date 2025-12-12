# scripts/build_grid.py
# Output: Throughput-only list and grid from your ACI Excel.
# For a given target IATA:
#   - finds 10 closest airports within the target region group by total passengers
#   - finds 5 closest airports out of region by total passengers
# Renders TWO sections:
#   1) Regional peers
#   2) International (out-of-region) peers
# Exposes build_grid(...). Also runnable as a script to write docs/grid.html.

import os, re, argparse, io
import numpy as np  # kept in case you later extend logic
import pandas as pd
import requests
from bs4 import BeautifulSoup

EXCEL_SHEET = "Working Global"

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
  grid-template-columns:repeat(5,minmax(140px,1fr));
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

def _dev(val, target):
    """Percentage deviation vs target, as a percent of target."""
    if pd.isna(val) or pd.isna(target):
        return ""
    if abs(target) < 1e-9:
        return ""
    diff_pct = (float(val) - float(target)) / float(target) * 100.0
    return _fmt_pct(diff_pct, signed=True, decimals=1)

def _resolve_sheet_name(excel_path: str, desired: str) -> str:
    xl = pd.ExcelFile(excel_path)
    want = (desired or "").strip().lower()
    for s in xl.sheet_names:
        if (s or "").strip().lower() == want:
            return s
    raise ValueError(f"Worksheet named '{desired}' not found. Available sheets: {xl.sheet_names}")

def fetch_aca_html(timeout: int = 45) -> str:
    url = "https://www.airportcarbonaccreditation.org/accredited-airports/"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ACA-Grid-Bot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_aca_regions(html: str) -> pd.DataFrame:
    """
    Return dataframe with columns: iata, region_group
    Using ACA Region to derive region_group buckets:
      - North America + Latin America & the Caribbean -> Americas
      - Europe -> Europe
      - UKIMEA -> UKIMEA
      - Asia Pacific -> Asia Pacific
      - else -> Other
    """
    soup = BeautifulSoup(html, "lxml")
    dfs = []

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
    aca = raw.rename(
        columns={
            "Airport code": "iata",
            "Region": "region",
        }
    )[["iata", "region"]].copy()

    aca["iata"] = aca["iata"].astype(str).str.strip().str.upper()
    aca["region"] = aca["region"].astype(str).str.strip()

    def region_group(r: str) -> str:
        if r in ("North America", "Latin America & the Caribbean"):
            return "Americas"
        if r == "Europe":
            return "Europe"
        if r == "UKIMEA":
            return "UKIMEA"
        if r == "Asia Pacific":
            return "Asia Pacific"
        return "Other"

    aca["region_group"] = aca["region"].map(region_group)
    aca = aca.dropna(subset=["iata"]).drop_duplicates(subset=["iata"], keep="first")
    return aca[["iata", "region_group"]]

def _fallback_region_from_country(country: str) -> str:
    """
    Minimal fallback for airports not present in ACA table.
    This is intentionally small to avoid a large embedded mapping.
    """
    c = (country or "").strip().lower()

    if c in ("united states", "canada", "mexico"):
        return "Americas"

    if c in ("united kingdom", "england", "scotland", "wales", "northern ireland",
             "ireland", "united arab emirates", "saudi arabia", "qatar", "oman",
             "kuwait", "bahrain", "jordan", "israel", "lebanon", "turkey"):
        return "UKIMEA"

    # Per requirement: India belongs with UK + Middle East bucket
    if c == "india":
        return "UKIMEA"

    if c in ("australia", "new zealand"):
        return "Asia Pacific"

    return "Unknown"

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
    sheet = _resolve_sheet_name(excel_path, EXCEL_SHEET)

    df = pd.read_excel(
        excel_path,
        sheet_name=sheet,
        header=2,
        usecols="A,C,F,M",
    ).copy()

    df.columns = ["rank", "country", "iata", "total_passengers"]

    df["country"] = df["country"].astype(str).str.strip()
    df["iata"] = df["iata"].astype(str).str.strip().str.upper()
    df["total_passengers"] = pd.to_numeric(df["total_passengers"], errors="coerce")

    df = df.dropna(subset=["iata", "country", "total_passengers"]).copy()
    df = df[df["iata"].astype(str).str.len().between(2, 4)].copy()
    df = df.drop_duplicates(subset=["iata"], keep="first").reset_index(drop=True)

    # Derive region_group from ACA by IATA
    try:
        aca_html = fetch_aca_html()
        aca_regions = parse_aca_regions(aca_html)
        df = df.merge(aca_regions, on="iata", how="left")
    except Exception:
        df["region_group"] = None

    # Fallback for missing region_group
    df["region_group"] = df["region_group"].fillna("")
    missing_mask = df["region_group"].astype(str).str.strip().eq("")
    if missing_mask.any():
        df.loc[missing_mask, "region_group"] = df.loc[missing_mask, "country"].map(_fallback_region_from_country)

    # Enforce India rule even if ACA mapping differs
    india_mask = df["country"].astype(str).str.strip().str.lower().eq("india")
    if india_mask.any():
        df.loc[india_mask, "region_group"] = "UKIMEA"

    df["region_group"] = df["region_group"].fillna("Unknown").astype(str).str.strip()

    return df

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

def _nearest_sets(df, iata, in_n=IN_REGION_N, out_n=OUT_REGION_N):
    """
    Returns:
      target_row, regional_df, international_df, union_set
    """
    t = df.loc[df["iata"] == iata].iloc[0]
    cand = df[df["iata"] != iata].copy()

    target_region = str(t.get("region_group", "Unknown")).strip() or "Unknown"
    cand = cand.assign(abs_diff_pax=(cand["total_passengers"] - t["total_passengers"]).abs())

    # If we cannot determine region, fall back to a single global list
    # (regional populated, international empty).
    if target_region.lower() == "unknown":
        top_all = (
            cand.sort_values(["abs_diff_pax", "total_passengers"], ascending=[True, False])
                .drop_duplicates(subset=["iata"], keep="first")
                .head(in_n + out_n)
        )
        union = {iata} | set(top_all["iata"].astype(str).str.upper().tolist())
        return t, top_all, top_all.iloc[0:0].copy(), union

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

    union = {iata} | set(pd.concat([top_in["iata"], top_out["iata"]]).astype(str).str.upper().tolist())
    return t, top_in, top_out, union

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
    Renders two sections: Regional peers and International peers.
    """
    df = _load_aci(excel_path)
    if df[df["iata"] == iata].empty:
        raise ValueError(f"IATA '{iata}' not found in ACI file.")

    target_iata = iata.upper()
    target, regional, international, union = _nearest_sets(df, target_iata)

    target_total = df.loc[df["iata"] == target_iata, "total_passengers"].iloc[0]
    target_region = str(target.get("region_group", "Unknown")).strip() or "Unknown"

    regional_html = _grid_html(regional, "total_passengers", target_total, target_iata)
    international_html = _grid_html(international, "total_passengers", target_total, target_iata)

    header_title = f"{target_iata} - overview of airports with similar throughput."
    header_meta  = f"Target: {target_iata} - {_fmt_int(target_total)} passengers"
    doc_title = f"{target_iata} - Airports with similar passenger throughput"

    header = f"""
    <div class="header">
      <h3>{header_title}</h3>
      <div class="meta">{header_meta}</div>
    </div>"""

    regional_section = f"""
    <div class="row">
      <div class="header">
        <h3>{target_iata} - regional peers by similar throughput ({target_region})</h3>
      </div>
      <div class="grid">{regional_html}</div>
    </div>"""

    international_section = ""
    if international is not None and not international.empty:
        international_section = f"""
    <div class="row">
      <div class="header">
        <h3>{target_iata} - international peers by similar throughput</h3>
      </div>
      <div class="grid">{international_html}</div>
    </div>"""

    html = f"""<!doctype html><meta charset="utf-8">
<title>{doc_title}</title>
{CSS}
<div class="container">
  {header}
  {regional_section}
  {international_section}
</div>"""

    if out_html:
        os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)

    nearest_list = pd.concat([regional["iata"], international["iata"]]).tolist()

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
