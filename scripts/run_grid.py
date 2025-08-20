# scripts/run_grid.py
# Minimal runner: calls build_grid() and writes HTML to /docs.

import os
import time
import argparse

# Import the function from the sibling file in the same folder
from build_grid import build_grid

EXCEL_PATH = "data/ACI_2024_NA_Traffic.xlsx"  # update if your filename differs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iata", required=True)
    ap.add_argument("--wsize", type=float, required=True)
    ap.add_argument("--wgrowth", type=float, required=True)
    args = ap.parse_args()

    iata = args.iata.upper()
    wsize = float(args.wsize)
    wgrowth = float(args.wgrowth)

    # Build the grid (returns HTML + metadata)
    res = build_grid(EXCEL_PATH, iata, wsize, wgrowth)

    # Ensure output folders
    os.makedirs("docs", exist_ok=True)
    os.makedirs("docs/runs", exist_ok=True)

    # Unique run directory
    run_id = str(int(time.time()))
    run_dir = f"docs/runs/{iata}-{int(wsize)}-{int(wgrowth)}-{run_id}"
    os.makedirs(run_dir, exist_ok=True)

    # Write outputs
    html = res["html"]
    with open("docs/grid.html", "w", encoding="utf-8") as f:
        f.write(html)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(run_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    print("Wrote:")
    print("  docs/grid.html")
    print("  docs/index.html")
    print(f"  {run_dir}/index.html")

if __name__ == "__main__":
    main()
