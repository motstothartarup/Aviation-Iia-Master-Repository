export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST,OPTIONS",
      "Access-Control-Allow-Headers": "content-type",
    };
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    if (request.method !== "POST") {
      return new Response(JSON.stringify({ ok: false, error: "POST only" }), {
        status: 405,
        headers: { ...cors, "content-type": "application/json" },
      });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response(JSON.stringify({ ok: false, error: "Invalid JSON" }), {
        status: 400,
        headers: { ...cors, "content-type": "application/json" },
      });
    }

    const iata = String(body.iata || "").trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(iata)) {
      return new Response(JSON.stringify({ ok: false, error: "IATA must be 3 letters" }), {
        status: 400,
        headers: { ...cors, "content-type": "application/json" },
      });
    }

    const owner = env.GH_OWNER;
    const repo = env.GH_REPO;
    const workflow = env.GH_WORKFLOW_FILE;   // e.g. "run-both.yml"
    const ref = env.GH_WORKFLOW_REF;         // e.g. "global_integration"
    const token = env.GH_TOKEN;

    if (!owner || !repo || !workflow || !ref || !token) {
      return new Response(JSON.stringify({ ok: false, error: "Missing env vars/secrets" }), {
        status: 500,
        headers: { ...cors, "content-type": "application/json" },
      });
    }

    const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;

    const r = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Accept": "application/vnd.github+json",
        "User-Agent": "aviation-iia-run-endpoint",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref, inputs: { iata } }),
    });

    if (!r.ok) {
      const text = await r.text().catch(() => "");
      return new Response(JSON.stringify({ ok: false, error: "GitHub dispatch failed", status: r.status, detail: text }), {
        status: 502,
        headers: { ...cors, "content-type": "application/json" },
      });
    }

    return new Response(JSON.stringify({ ok: true, dispatched: true, iata }), {
      status: 200,
      headers: { ...cors, "content-type": "application/json" },
    });
  }
};
