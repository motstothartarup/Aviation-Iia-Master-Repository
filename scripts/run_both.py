# scripts/run_both.py
# One runner to build BOTH:
#   - docs/grid.html    (competitor grid from ACI Excel)
#   - docs/aca_table.html (ACA region board, defaulting to target's region)
# And a simple docs/index.html that shows both via iframes.
#
# Usage (in Actions or locally):
#   python scripts/run_both.py --iata LAX --wsize 85 --wgrowth 5

import os
import time
import argparse

# import sibling builders (same folder)
from build_grid import build_grid
from build_aca_table import build_aca_table_html

EXCEL_PATH = "data/ACI_2024_NA_Traffic.xlsx"  # adjust if your filename differs

INDEX_TEMPLATE = """<!doctype html><meta charset="utf-8">
<title>{title}</title>
<style>
  body{{margin:0;padding:24px;font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:#f6f8fb;color:#1f2937}}
  .wrap{{max-width:1200px;margin:0 auto}}
  h2{{margin:0 0 12px 0}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,.05);padding:12px 12px;margin:16px 0}}
  iframe{{width:100%;height:720px;border:0;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,.05)}}
  .muted{{color:#6b7280;font-size:13px}}
</style>
<div class="wrap">
  <h2>{title}</h2>

  <div class="card">
    <div class="muted">Competitor Grid</div>
    <iframe src="{grid_rel}"></iframe>
  </div>

  <div class="card">
    <div class="muted">ACA Airports by Region (defaulted to {iata})</div>
    <iframe src="{aca_rel}"></iframe>
  </div>
</div>
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iata", required=True)
    ap.add_argument("--wsize", type=float, required=True)
    ap.add_argument("--wgrowth", type=float, required=True)
    args = ap.parse_args()

    iata = args.iata.upper()
    wsize = float(args.wsize)
    wgrowth = float(args.wgrowth)

    os.makedirs("docs", exist_ok=True)
    os.makedirs("docs/runs", exist_ok=True)

    # --- Build grid ---
    grid_res = build_grid(EXCEL_PATH, iata, wsize, wgrowth)   # returns {"html", "union", "target", "weights"}
    grid_html = grid_res["html"]

    with open("docs/grid.html", "w", encoding="utf-8") as f:
        f.write(grid_html)

    # --- Build ACA table (default region = that airport's; highlight target) ---
    aca_html, _aca_df = build_aca_table_html(iata)
    with open("docs/aca_table.html", "w", encoding="utf-8") as f:
        f.write(aca_html)

    # --- Assemble simple dashboard index ---
    title = f"{iata} — Grid + ACA"
    index_html = INDEX_TEMPLATE.format(
        title=title,
        grid_rel="grid.html",
        aca_rel="aca_table.html",
        iata=iata,
    )
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

    # --- Also write a frozen snapshot under /docs/runs/.../index.html ---
    run_id = str(int(time.time()))
    run_dir = f"docs/runs/{iata}-{int(wsize)}-{int(wgrowth)}-{run_id}"
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, "grid.html"), "w", encoding="utf-8") as f:
        f.write(grid_html)
    with open(os.path.join(run_dir, "aca_table.html"), "w", encoding="utf-8") as f:
        f.write(aca_html)
    snap_index = INDEX_TEMPLATE.format(
        title=title,
        grid_rel="grid.html",
        aca_rel="aca_table.html",
        iata=iata,
    )
    with open(os.path.join(run_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(snap_index)

    print("Wrote:")
    print("  docs/grid.html")
    print("  docs/aca_table.html")
    print("  docs/index.html")
    print(f"  {run_dir}/index.html")

if __name__ == "__main__":
    main()
