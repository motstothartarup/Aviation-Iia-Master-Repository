# scripts/run_all.py
# Build ALL outputs + dashboard with a "Reset" modal that:
#  - lets you switch to any prior run (from docs/runs/index.json)
#  - provides a quick link to the GitHub Action to run a NEW build
#
# Usage:
#   python scripts/run_all.py --iata LAX --wsize 85 --wgrowth 5

import os
import time
import json
import argparse

from build_grid import build_grid
from build_aca_table import build_aca_table_html
from build_map import build_map

EXCEL_PATH = "data/ACI_2024_NA_Traffic.xlsx"  # adjust if needed
DOCS_DIR = "docs"
RUNS_DIR = os.path.join(DOCS_DIR, "runs")
MANIFEST = os.path.join(RUNS_DIR, "index.json")

DASHBOARD_TEMPLATE = """<!doctype html><meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root {{
    --bg:#f6f8fb; --ink:#1f2937; --muted:#6b7280; --border:#e5e7eb; --card:#fff; --accent:#0d6efd;
  }}
  html,body {{ margin:0; padding:0; background:var(--bg); color:var(--ink); font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }}
  .wrap {{ max-width:1200px; margin:0 auto; padding:24px; }}
  .topbar {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; gap:12px; flex-wrap:wrap; }}
  h2 {{ margin:0; font-size:22px; }}
  .btn {{
    display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:10px;
    border:1px solid var(--border); background:#fff; cursor:pointer; font-size:14px;
  }}
  .btn:hover {{ background:#fafbfc; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.05); padding:12px 12px; margin:16px 0; }}
  .muted {{ color:var(--muted); font-size:13px; margin-bottom:8px; }}
  iframe {{ width:100%; height:720px; border:0; border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.05); background:#fff; }}
  @media (max-width: 900px) {{ iframe {{ height: 600px; }} }}

  /* Modal */
  .modal-backdrop {{
    position:fixed; inset:0; background:rgba(0,0,0,.35); display:none; align-items:center; justify-content:center; z-index:9999;
  }}
  .modal {{
    width:min(640px, 94vw); background:#fff; border-radius:12px; box-shadow:0 20px 50px rgba(0,0,0,.25);
    border:1px solid var(--border); padding:16px;
  }}
  .modal h3 {{ margin:0 0 8px 0; font-size:18px; }}
  .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:12px; }}
  label {{ display:block; font-size:13px; color:#374151; margin-bottom:4px; }}
  input, select {{
    width:100%; font:14px/1.2 inherit; padding:8px 10px; border-radius:8px; border:1px solid var(--border); background:#fff;
  }}
  .row {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  .actions {{ display:flex; gap:10px; justify-content:flex-end; margin-top:12px; }}
  .btn-primary {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .btn-primary:hover {{ filter:brightness(0.95); }}
  .hint {{ font-size:12px; color:var(--muted); }}
</style>

<div class="wrap">
  <div class="topbar">
    <h2 id="title">{title}</h2>
    <div class="row">
      <button id="btnReset" class="btn" type="button">Reset / Choose another run</button>
      <a id="btnAction" class="btn" href="#" target="_blank" rel="noopener">Run new build</a>
    </div>
  </div>

  <div class="card">
    <div class="muted">Competitor Grid</div>
    <iframe id="gridFrame" src="{grid_rel}" title="Competitor Grid"></iframe>
  </div>

  <div class="card">
    <div class="muted">ACA Airports by Region (defaulted to {iata})</div>
    <iframe id="acaFrame" src="{aca_rel}" title="ACA Region Table"></iframe>
  </div>

  <div class="card">
    <div class="muted">ACA Map (Americas)</div>
    <iframe id="mapFrame" src="{map_rel}" title="ACA Map"></iframe>
  </div>
</div>

<!-- Modal -->
<div class="modal-backdrop" id="modalBg" aria-hidden="true">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
    <h3 id="modalTitle">Reset / Choose another run</h3>
    <div class="grid">
      <div>
        <label for="runSelect">Pick from previous runs</label>
        <select id="runSelect"><option value="">Loading…</option></select>
        <div class="hint">Switch instantly to any run that’s already built.</div>
      </div>
      <div>
        <label>Or set a new target</label>
        <div class="row">
          <div style="flex:1; min-width:120px;">
            <label for="iataInput">IATA</label>
            <input id="iataInput" placeholder="e.g., LAX" />
          </div>
          <div style="flex:1; min-width:120px;">
            <label for="wsizeInput">Weight: total passengers</label>
            <input id="wsizeInput" placeholder="85" />
          </div>
          <div style="flex:1; min-width:120px;">
            <label for="wgrowthInput">Weight: growth</label>
            <input id="wgrowthInput" placeholder="5" />
          </div>
        </div>
        <div class="hint">Click “Run new build” above after entering values; the page updates once the Action finishes.</div>
      </div>
    </div>
    <div class="actions">
      <button class="btn" id="btnClose" type="button">Close</button>
      <button class="btn btn-primary" id="btnApplyRun" type="button">View selected run</button>
    </div>
  </div>
</div>

<script>
(function(){
  const runManifestUrl = "runs/index.json"; // relative to /docs
  const gridFrame = document.getElementById('gridFrame');
  const acaFrame  = document.getElementById('acaFrame');
  const mapFrame  = document.getElementById('mapFrame');
  const titleEl   = document.getElementById('title');

  // Reset modal wiring
  const modalBg   = document.getElementById('modalBg');
  const btnReset  = document.getElementById('btnReset');
  const btnClose  = document.getElementById('btnClose');
  const runSelect = document.getElementById('runSelect');
  const btnApply  = document.getElementById('btnApplyRun');

  // Link to GitHub Actions "workflow_dispatch" page (user runs it manually)
  // NOTE: Replace ORG/REPO and workflow filename if different.
  const actionsUrl = "https://github.com/{gh_owner}/{gh_repo}/actions/workflows/run-both.yml";
  const btnAction  = document.getElementById('btnAction');
  btnAction.href = actionsUrl;

  function openModal(){ modalBg.style.display = "flex"; modalBg.setAttribute("aria-hidden", "false"); }
  function closeModal(){ modalBg.style.display = "none"; modalBg.setAttribute("aria-hidden", "true"); }

  btnReset.addEventListener('click', openModal);
  btnClose.addEventListener('click', closeModal);
  modalBg.addEventListener('click', (e)=>{ if (e.target === modalBg) closeModal(); });

  // Load manifest of prior runs
  async function loadRuns(){
    try{
      const res = await fetch(runManifestUrl, {cache:"no-store"});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      renderRunOptions(data.runs || []);
    } catch(err){
      runSelect.innerHTML = '<option value="">No manifest found</option>';
    }
  }

  function renderRunOptions(runs){
    if (!runs.length){
      runSelect.innerHTML = '<option value="">No prior runs</option>';
      return;
    }
    // newest first
    runs.sort((a,b)=> (b.ts||0) - (a.ts||0));
    runSelect.innerHTML = runs.map(r=>{
      const label = `${r.iata} — ws:${r.wsize} wg:${r.wgrowth} — ${new Date((r.ts||0)*1000).toLocaleString()}`;
      return `<option value="${r.path}">${label}</option>`;
    }).join("");
  }

  btnApply.addEventListener('click', ()=>{
    const p = runSelect.value;
    if (!p) return;
    // Switch iframes to the selected run
    gridFrame.src = p + "/grid.html";
    acaFrame.src  = p + "/aca_table.html";
    mapFrame.src  = p + "/aca_map.html";
    // Update title (parse path suffix)
    const parts = p.split("/").pop().split("-");
    const iata  = parts[0] || "";
    const wsize = parts[1] || "";
    const wg    = parts[2] || "";
    titleEl.textContent = `${iata} — Grid + ACA + Map (ws:${wsize} wg:${wg})`;
    closeModal();
  });

  loadRuns();
})();
</script>
"""

def _load_manifest():
    if not os.path.exists(MANIFEST):
        return {"runs": []}
    try:
        with open(MANIFEST, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"runs": []}

def _save_manifest(man):
    os.makedirs(RUNS_DIR, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)

def main():
    ap = argparse.ArgumentParser(description="Build grid + ACA table + ACA map and publish to docs/")
    ap.add_argument("--iata", required=True, help="Target airport IATA code (e.g., LAX)")
    ap.add_argument("--wsize", type=float, required=True, help="Weight for total passengers (e.g., 85)")
    ap.add_argument("--wgrowth", type=float, required=True, help="Weight for growth (e.g., 5)")
    ap.add_argument("--gh-owner", default=os.environ.get("GITHUB_REPOSITORY", "owner/repo").split("/")[0])
    ap.add_argument("--gh-repo",  default=os.environ.get("GITHUB_REPOSITORY", "owner/repo").split("/")[1] if "/" in os.environ.get("GITHUB_REPOSITORY","") else "repo")
    ap.add_argument("--workflow-file", default="run-both.yml")  # if your workflow file name differs, change here
    args = ap.parse_args()

    iata = args.iata.upper()
    wsize = float(args.wsize)
    wgrowth = float(args.wgrowth)

    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)

    # --- 1) Build competitor grid ---
    grid_res = build_grid(EXCEL_PATH, iata, wsize, wgrowth)
    grid_html = grid_res["html"]
    with open(os.path.join(DOCS_DIR, "grid.html"), "w", encoding="utf-8") as f:
        f.write(grid_html)

    # --- 2) Build ACA table ---
    aca_html, _aca_df = build_aca_table_html(iata)
    with open(os.path.join(DOCS_DIR, "aca_table.html"), "w", encoding="utf-8") as f:
        f.write(aca_html)

    # --- 3) Build ACA map (highlight grid competitors) ---
    highlight = set(grid_res.get("union", []))
    fmap = build_map(highlight_iatas=highlight)
    fmap.save(os.path.join(DOCS_DIR, "aca_map.html"))

    # --- 4) Assemble combined dashboard at docs/index.html ---
    # Wire the Action link with your org/repo + workflow filename.
    title = f"{iata} — Grid + ACA + Map"
    dash_html = DASHBOARD_TEMPLATE.format(
        title=title,
        grid_rel="grid.html",
        aca_rel="aca_table.html",
        map_rel="aca_map.html",
        iata=iata,
        gh_owner=args.gh_owner,
        gh_repo=args.gh_repo,
    ).replace("run-both.yml", args.workflow_file)
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(dash_html)

    # --- 5) Frozen snapshot + update manifest ---
    ts = int(time.time())
    run_dir = f"{RUNS_DIR}/{iata}-{int(wsize)}-{int(wgrowth)}-{ts}"
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, "grid.html"), "w", encoding="utf-8") as f:
        f.write(grid_html)
    with open(os.path.join(run_dir, "aca_table.html"), "w", encoding="utf-8") as f:
        f.write(aca_html)
    fmap.save(os.path.join(run_dir, "aca_map.html"))

    # Maintain runs/index.json manifest (newest appended)
    manifest = _load_manifest()
    manifest.setdefault("runs", [])
    manifest["runs"].append({
        "ts": ts,
        "iata": iata,
        "wsize": wsize,
        "wgrowth": wgrowth,
        "path": f"runs/{iata}-{int(wsize)}-{int(wgrowth)}-{ts}"
    })
    # Keep last 100 to avoid bloat
    manifest["runs"] = manifest["runs"][-100:]
    _save_manifest(manifest)

    print("Wrote:")
    print("  docs/grid.html")
    print("  docs/aca_table.html")
    print("  docs/aca_map.html")
    print("  docs/index.html")
    print(f"  {run_dir}/index.html")
    print("Updated manifest:", MANIFEST)

if __name__ == "__main__":
    main()
