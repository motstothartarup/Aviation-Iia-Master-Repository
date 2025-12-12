# scripts/run_all.py
# Build ALL outputs + dashboard with a "Reset" modal to switch between prior runs
# and a link to trigger a new build via workflow_dispatch.

import os
import time
import json
import argparse

from build_grid import build_grid
from build_aca_table import build_aca_table_html
from build_map import build_map

EXCEL_PATH = "data/Copy of ACI 2024 North America Traffic Report (1).xlsx"
DOCS_DIR = "docs"
RUNS_DIR = os.path.join(DOCS_DIR, "runs")
MANIFEST = os.path.join(RUNS_DIR, "index.json")

# Use simple tokens instead of .format() to avoid brace conflicts in CSS/JS.
DASHBOARD_TEMPLATE = r"""<!doctype html><meta charset="utf-8">
<title>__TITLE__</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root {
    --bg:#f6f8fb; --ink:#1f2937; --muted:#6b7280; --border:#e5e7eb; --card:#fff; --accent:#0d6efd;
  }
  html,body { margin:0; padding:0; background:var(--bg); color:var(--ink); font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }
  .wrap { max-width:1200px; margin:0 auto; padding:16px 16px 20px 16px; }
  .topbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; gap:12px; flex-wrap:wrap; }
  h2 { margin:0; font-size:20px; }
  .btn {
    display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:10px;
    border:1px solid var(--border); background:#fff; cursor:pointer; font-size:14px;
  }
  .btn:hover { background:#fafbfc; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.05); padding:10px 10px; margin:12px 0; }
  .muted { color:var(--muted); font-size:13px; margin-bottom:6px; }
  iframe { width:100%; height:620px; border:0; border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.05); background:#fff; }
  @media (max-width: 900px) { iframe { height: 520px; } }

  /* Modal */
  .modal-backdrop {
    position:fixed; inset:0; background:rgba(0,0,0,.35); display:none; align-items:center; justify-content:center; z-index:9999;
  }
  .modal {
    width:min(640px, 94vw); background:#fff; border-radius:12px; box-shadow:0 20px 50px rgba(0,0,0,.25);
    border:1px solid var(--border); padding:16px;
  }
  .modal h3 { margin:0 0 8px 0; font-size:18px; }
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
  label { display:block; font-size:13px; color:#374151; margin-bottom:4px; }
  input, select {
    width:100%; font:14px/1.2 inherit; padding:8px 10px; border-radius:8px; border:1px solid var(--border); background:#fff;
  }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .actions { display:flex; gap:10px; justify-content:flex-end; margin-top:12px; }
  .btn-primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  .btn-primary:hover { filter:brightness(0.95); }
  .hint { font-size:12px; color:var(--muted); }
</style>

<div class="wrap">
  <div class="topbar">
    <h2 id="title">__TITLE__</h2>
    <div class="row">
      <button id="btnReset" class="btn" type="button">Reset / Choose another run</button>
      <a id="btnAction" class="btn" href="__ACTIONS_URL__" target="_blank" rel="noopener">Run new build</a>
    </div>
  </div>

  <div class="card">
    <div class="muted">Similar-throughput airports grid</div>
    <iframe id="gridFrame" src="__GRID__" title="Similar-throughput grid"></iframe>
  </div>

  <div class="card">
    <div class="muted">ACA scores for airports with similar throughput (__IATA__)</div>
    <iframe id="acaFrame" src="__ACA__" title="ACA scores table"></iframe>
  </div>

  <div class="card">
    <div class="muted">ACA map (Americas)</div>
    <iframe id="mapFrame" src="__MAP__" title="ACA Map"></iframe>
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
        <div class="hint">Switch instantly to any run that is already built.</div>
      </div>
      <div>
        <label>Run a new build</label>
        <div class="hint">
          Click “Run new build” at the top right of this page, then enter a new IATA code
          in the GitHub Actions form. After the Action finishes, refresh this page and pick
          the new run from the list on the left.
        </div>
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
  const runManifestUrl = "runs/index.json";
  const gridFrame = document.getElementById('gridFrame');
  const acaFrame  = document.getElementById('acaFrame');
  const mapFrame  = document.getElementById('mapFrame');
  const titleEl   = document.getElementById('title');

  const modalBg   = document.getElementById('modalBg');
  const btnReset  = document.getElementById('btnReset');
  const btnClose  = document.getElementById('btnClose');
  const runSelect = document.getElementById('runSelect');
  const btnApply  = document.getElementById('btnApplyRun');

  // Relay ACA table clicks to the map iframe
  window.addEventListener('message', (ev) => {
    const data = ev.data || {};
    if (!data || data.type !== 'ACA_TOGGLE_CODE') return;
    try {
      if (mapFrame && mapFrame.contentWindow) {
        mapFrame.contentWindow.postMessage(data, '*');
      }
    } catch (e) {}
  });

  let runsCache = [];

  function openModal(){
    modalBg.style.display = "flex";
    modalBg.setAttribute("aria-hidden", "false");
  }
  function closeModal(){
    modalBg.style.display = "none";
    modalBg.setAttribute("aria-hidden", "true");
  }

  btnReset.addEventListener('click', openModal);
  btnClose.addEventListener('click', closeModal);
  modalBg.addEventListener('click', (e)=>{
    if (e.target === modalBg) closeModal();
  });

  async function loadRuns(){
    try{
      const res = await fetch(runManifestUrl, {cache:"no-store"});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      runsCache = data.runs || [];
      renderRunOptions(runsCache);
    } catch(err){
      runSelect.innerHTML = '<option value="">No manifest found</option>';
    }
  }

  function renderRunOptions(runs){
    if (!runs.length){
      runSelect.innerHTML = '<option value="">No prior runs</option>';
      return;
    }
    runs.sort((a,b)=> (b.ts||0) - (a.ts||0));
    runSelect.innerHTML = runs.map(r=>{
      const label = `${r.iata} — ${new Date((r.ts||0)*1000).toLocaleString()}`;
      return `<option value="${r.path}">${label}</option>`;
    }).join("");
  }

  btnApply.addEventListener('click', ()=>{
    const p = runSelect.value;
    if (!p) return;
    const run = runsCache.find(r => r.path === p) || {};
    gridFrame.src = p + "/grid.html";
    acaFrame.src  = p + "/aca_table.html";
    mapFrame.src  = p + "/aca_map.html";
    const iata  = run.iata || "";
    titleEl.textContent = `${iata} — Grid + ACA + Map`;
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
    ap = argparse.ArgumentParser(
        description="Build grid + ACA table + ACA map and publish to docs/"
    )
    ap.add_argument(
        "--iata",
        required=True,
        help="Target airport IATA code (e.g., LAX)",
    )
    ap.add_argument(
        "--gh-owner",
        default=os.environ.get("GITHUB_REPOSITORY", "owner/repo").split("/")[0],
    )
    ap.add_argument(
        "--gh-repo",
        default=(
            os.environ.get("GITHUB_REPOSITORY", "owner/repo").split("/")[1]
            if "/" in os.environ.get("GITHUB_REPOSITORY", "")
            else "repo"
        ),
    )
    ap.add_argument(
        "--workflow-file",
        default="run-both.yml",
        help="Workflow file name used for the 'Run new build' link.",
    )
    args = ap.parse_args()

    iata = args.iata.upper()

    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)

    # 1) Grid (throughput-only similarity)
    grid_out_path = os.path.join(DOCS_DIR, "grid.html")
    grid_res = build_grid(EXCEL_PATH, iata, out_html=grid_out_path)
    grid_html = grid_res["html"]

    # 2) ACA table (auto-discovers competitors from docs/grid.html)
    aca_html, _aca_df = build_aca_table_html(iata)
    with open(os.path.join(DOCS_DIR, "aca_table.html"), "w", encoding="utf-8") as f:
        f.write(aca_html)

    # 3) Map (highlight grid competitors)
    highlight = set(grid_res.get("union", []))
    fmap = build_map(target_iata=iata, highlight_iatas=highlight)
    fmap.save(os.path.join(DOCS_DIR, "aca_map.html"))

    # 4) Dashboard
    actions_url = (
        f"https://github.com/{args.gh_owner}/{args.gh_repo}"
        f"/actions/workflows/{args.workflow_file}"
    )
    dash_html = (
        DASHBOARD_TEMPLATE
        .replace("__TITLE__", f"{iata} — Grid + ACA + Map")
        .replace("__GRID__", "grid.html")
        .replace("__ACA__", "aca_table.html")
        .replace("__MAP__", "aca_map.html")
        .replace("__IATA__", iata)
        .replace("__ACTIONS_URL__", actions_url)
    )
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(dash_html)

    # 5) Snapshot + manifest
    ts = int(time.time())
    run_dir = f"{RUNS_DIR}/{iata}-{ts}"
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, "grid.html"), "w", encoding="utf-8") as f:
        f.write(grid_html)
    with open(os.path.join(run_dir, "aca_table.html"), "w", encoding="utf-8") as f:
        f.write(aca_html)
    fmap.save(os.path.join(run_dir, "aca_map.html"))

    manifest = _load_manifest()
    manifest.setdefault("runs", [])
    manifest["runs"].append(
        {
            "ts": ts,
            "iata": iata,
            "path": f"runs/{iata}-{ts}",
        }
    )
    # Keep only the most recent 100
    manifest["runs"] = manifest["runs"][-100:]
    _save_manifest(manifest)

    print("Wrote:")
    print("  docs/grid.html")
    print("  docs/aca_table.html")
    print("  docs/aca_map.html")
    print("  docs/index.html")
    print(f"  {run_dir}/grid.html")
    print(f"  {run_dir}/aca_table.html")
    print(f"  {run_dir}/aca_map.html")
    print("Updated manifest:", MANIFEST)

if __name__ == "__main__":
    main()
