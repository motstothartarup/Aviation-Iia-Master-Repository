# at the very top of run_grid.py
import os, sys
sys.path.append(os.path.dirname(__file__))  # allow local imports
from build_grid import build_grid

# scripts/run_grid.py
# Run ONLY the competitor grid and write HTML into docs/.

import os, argparse, time
from scripts.build_grid import build_grid

EXCEL_PATH = "data/ACI_2024_NA_Traffic.xlsx"  # adjust only if your filename differs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iata", required=True)
    ap.add_argument("--wsize", type=float, required=True)
    ap.add_argument("--wgrowth", type=float, required=True)
    args = ap.parse_args()

    iata   = args.iata.upper()
    wsize  = float(args.wsize)
    wgrowth = float(args.wgrowth)

    # Run grid builder
    res = build_grid(EXCEL_PATH, iata, wsize, wgrowth)

    # Where to write
    os.makedirs("docs", exist_ok=True)
    run_id  = str(int(time.time()))
    run_dir = f"docs/runs/{iata}-{int(wsize)}-{int(wgrowth)}-{run_id}"
    os.makedirs(run_dir, exist_ok=True)

    # Write a standalone page for the grid (root and per-run)
    with open("docs/grid.html", "w", encoding="utf-8") as f:
        f.write(res["html"])
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(res["html"])
    with open(os.path.join(run_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(res["html"])

    print("Wrote:")
    print("  docs/grid.html")
    print("  docs/index.html")
    print(f"  {run_dir}/index.html")

if __name__ == "__main__":
    main()
