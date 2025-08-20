# scripts/run_all.py
# One runner to build ALL THREE outputs and assemble a single dashboard page:
#   - docs/grid.html         (competitor grid from ACI Excel)
#   - docs/aca_table.html    (ACA region board; defaults to target airport's region and highlights it)
#   - docs/aca_map.html      (ACA Americas map; highlights competitor airports from the grid)
# Plus:
#   - docs/index.html        (dashboard with 3 iframes)
#   - docs/runs/<iata>-<wsize>-<wgrowth>-<ts>/... (frozen snapshot of the above)
#
# Usage (from GitHub Actions or locally):
#   python scripts/run_all.py --iata LAX --wsize 85 --wgrowth 5

import os
import time
import argparse

# Import builders from sibling files in the same folder.
# (No package prefix = simplest cross-platform import when running as a script.)
from build_grid import build_grid
from build_aca_table import build_aca_table_html
from build_map import build_map

EXCEL_PATH = "data/ACI_2024_NA_Traffic.xlsx"  # adjust filename if yours differs

DASHBOARD_TEMPLATE = """<!doctype html><meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  :root {{
    --bg:#f6f8fb; --ink:#1f2937; --muted:#6b7280; --border:#e5e7eb; --card:#fff;
  }}
  html,body {{ margin:0; padding:0; background:var(--bg); color:var(--ink); font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }}
  .wrap {{ max-width:1200px; margin:0 auto; padding:24px; }}
  h2 {{ margin:0 0 12px 0; font-size:22px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.05); padding:12px 12px; margin:16px 0; }}
  .muted {{ color:var(--muted); font-size:13px; margin-bottom:8px; }}
  iframe {{ width:100%; height:720px; border:0; border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.05); background:#fff; }}
  @media (max-width: 900px) {{
    iframe {{ height: 600px; }}
  }}
</style>
<div class="wrap">
  <h2>{title}</h2>

  <div class="card">
    <div class="muted">Competitor Grid</div>
    <iframe src="{grid_rel}" title="Competitor Grid"></iframe>
  </div>

  <div class="card">
    <div class="muted">ACA Airports by Region (defaulted to {iata})</div>
    <iframe src="{aca_rel}" title="ACA Region Table"></iframe>
  </div>

  <div class="card">
    <div class="muted">ACA Map (Americas)</div>
    <iframe src="{map_rel}" title="ACA Map"></iframe>
  </div>
</div>
"""

def main():
    ap = argparse.ArgumentParser(description="Build grid + ACA table + ACA map and publish to docs/")
    ap.add_argument("--iata", required=True, help="Target airport IATA code (e.g., LAX)")
    ap.add_argument("--wsize", type=float, required=True, help="Weight for total passengers (e.g., 85)")
    ap.add_argument("--wgrowth", type=float, required=True, help="Weight for growth (e.g., 5)")
    args = ap.parse_args()

    iata = args.iata.upper()
    wsize = float(args.wsize)
    wgrowth = float(args.wgrowth)

    os.makedirs("docs", exist_ok=True)
    os.makedirs("docs/runs", exist_ok=True)

    # --- 1) Build competitor grid ---
    # Expected return from build_grid: {"html": ..., "union": [IATAs...], "target": ..., "weights": {...}}
    grid_res = build_grid(EXCEL_PATH, iata, wsize, wgrowth)
    grid_html = grid_res["html"]
    with open("docs/grid.html", "w", encoding="utf-8") as f:
        f.write(grid_html)

    # --- 2) Build ACA table (default region uses the target IATA; highlight it) ---
    aca_html, _aca_df = build_aca_table_html(iata)
    with open("docs/aca_table.html", "w", encoding="utf-8") as f:
        f.write(aca_html)

    # --- 3) Build ACA map, highlighting competitor airports from the grid's union ---
    highlight = set(grid_res.get("union", []))
    fmap = build_map(highlight_iatas=highlight)
    fmap.save("docs/aca_map.html")

    # --- 4) Assemble combined dashboard index ---
    title = f"{iata} — Grid + ACA + Map"
    index_html = DASHBOARD_TEMPLATE.format(
        title=title,
        grid_rel="grid.html",
        aca_rel="aca_table.html",
        map_rel="aca_map.html",
        iata=iata,
    )
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

    # --- 5) Also write a frozen snapshot under /docs/runs/... ---
    run_id = str(int(time.time()))
    run_dir = f"docs/runs/{iata}-{int(wsize)}-{int(wgrowth)}-{run_id}"
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, "grid.html"), "w", encoding="utf-8") as f:
        f.write(grid_html)
    with open(os.path.join(run_dir, "aca_table.html"), "w", encoding="utf-8") as f:
        f.write(aca_html)
    fmap.save(os.path.join(run_dir, "aca_map.html"))

    snap_index = DASHBOARD_TEMPLATE.format(
        title=title,
        grid_rel="grid.html",
        aca_rel="aca_table.html",
        map_rel="aca_map.html",
        iata=iata,
    )
    with open(os.path.join(run_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(snap_index)

    print("Wrote:")
    print("  docs/grid.html")
    print("  docs/aca_table.html")
    print("  docs/aca_map.html")
    print("  docs/index.html")
    print(f"  {run_dir}/index.html")

if __name__ == "__main__":
    main()
