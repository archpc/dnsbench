/* DNSBench frontend: minimal JS — SSE event handling, WASM interop, table render.
   No frameworks. Rendering work lives in viz.wasm; JS only moves data. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  running: false,
  pendingConfirm: false,
  points: null,          // {cat_names, points}
  wasm: null,            // {exports, memory, ingestBuf, layoutBuf, paletteBuf}
  latestRun: null,
  selectedRun: "",
  runs: [],
};

/* ---------- SSE ---------- */
function connectEvents() {
  const es = new EventSource("/events");
  const map = {
    state: onState,
    stage: onStage,
    progress: onProgress,
    server_done: onServerDone,
    pool_updated: onPoolUpdated,
    run_complete: onRunComplete,
    job_error: onJobError,
    job_done: onJobDone,
    cancelled: onJobDone,
  };
  for (const [ev, fn] of Object.entries(map)) es.addEventListener(ev, (e) => {
    let data = {};
    try { data = JSON.parse(e.data); } catch { }
    fn(data);
  });
  es.onopen = () => setStatus("connected");
  es.onerror = () => { setStatus("reconnecting…"); };
}

function setStatus(t) { $("status-text").textContent = t; }

function renderRunStats(d) {
  // header stat chips: candidates, pool size, runs stored
  const parts = [];
  if (d.n_candidates) parts.push(`${d.n_candidates.toLocaleString()} candidates`);
  if (d.pool && d.pool.eligible) parts.push(`pool ${d.pool.eligible.toLocaleString()}`);
  if (d.n_runs) parts.push(`${d.n_runs} runs stored`);
  $("run-stats").textContent = parts.join(" · ");
}

function onState(d) {
  state.running = d.running;
  if ("pending_confirm" in d) state.pendingConfirm = d.pending_confirm;
  renderRunStats(d);
  setControls();
}

function onStage(d) {
  const badge = $("stage-badge");
  badge.textContent = d.stage;
  badge.className = "stage-badge " + (d.stage === "starting" ? "live" : "idle");
  if (d.stage === "discovery" || d.stage === "deepbench") {
    showProgress(d.stage === "discovery");
    $("progress-label").textContent = d.stage === "discovery" ? "discovery" : `deep bench ${d.n_servers || ""}`;
  }
}

function onProgress(d) {
  const pct = d.total ? Math.round((d.done / d.total) * 100) : 0;
  $("progress-bar").style.setProperty("--w", pct + "%");
  let label = `${d.stage}: ${d.done}/${d.total}`;
  if (d.alive !== null && d.alive !== undefined) label += ` alive ${d.alive}`;
  $("progress-label").textContent = label;
  if (d.stage === "liveness") {
    // stream liveness into the field: mark unknown->alive/dead progressively
    vizLivenessTick(d.done, d.alive);
  }
}

const liveResults = new Map();
function onServerDone(d) {
  liveResults.set(d.ip, d);
  vizPointTick(d);
}

function onPoolUpdated() { refreshPoints(); }

function onRunComplete(d) {
  state.latestRun = d.run_dir;
  loadRunList().then(() => {
    $("run-select").value = d.run_dir;
    loadRun(d.run_dir);
  });
  $("stage-badge").textContent = "idle";
  hideProgress();
  vizFlashRun();
}

function onJobError(d) {
  const el = $("job-error");
  el.hidden = false;
  el.textContent = "error: " + d.error;
  $("stage-badge").textContent = "error";
  $("stage-badge").className = "stage-badge err";
  hideProgress();
}

function onJobDone() {
  state.running = false;
  setControls();
  $("stage-badge").textContent = "idle";
  $("stage-badge").className = "stage-badge idle";
  hideProgress();
}

/* ---------- controls ---------- */
async function refreshState() {
  onState(await (await fetch("/api/state")).json());
}

function setControls() {
  const busy = state.running || state.pendingConfirm;
  $("btn-run").disabled = busy;
  $("btn-cancel").disabled = !state.running;
}

$("run-form").addEventListener("submit", (e) => {
  e.preventDefault();
  $("job-error").hidden = true;
  $("confirm-box").hidden = true;
  api("/api/run", {
    n: +$("f-n").value,
    workers: +$("f-workers").value,
    timeout_s: +$("f-timeout").value,
    screening: $("f-screening").value,
  }).then((r) => {
    if (r.confirm_required) {
      $("confirm-reason").textContent = "Large operation: " + r.confirm_required.join(" · ");
      $("confirm-box").hidden = false;
      state.pendingConfirm = true;   // awaiting confirmation, not running
      state.running = false;
    } else if (r.error) {
      onJobError({ error: r.error });
      state.pendingConfirm = false;
    } else {
      state.pendingConfirm = false;
      state.running = true;
    }
    setControls();
  });
});

$("btn-cancel").addEventListener("click", () => api("/api/cancel", {}));

$("btn-confirm-yes").addEventListener("click", async () => {
  $("confirm-box").hidden = true;
  const r = await api("/api/confirm", { confirmed: true });
  if (r.error) {
    onJobError({ error: r.error });
    state.pendingConfirm = false;
  } else {
    state.pendingConfirm = false;
    state.running = true;
  }
  setControls();
});

$("btn-confirm-no").addEventListener("click", async () => {
  $("confirm-box").hidden = true;
  await api("/api/confirm", { confirmed: false });
  state.pendingConfirm = false;      // decline resets controls (P2 fix)
  await refreshState();
  setControls();
});

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function showProgress(isDiscovery) {
  $("progress-wrap").hidden = false;
  $("progress-bar").style.setProperty("--w", "0%");
}
function hideProgress() { $("progress-wrap").hidden = true; }

/* ---------- tables ---------- */
async function loadRunList() {
  const d = await (await fetch("/api/runs")).json();
  state.runs = d.runs;
  const sel = $("run-select"), ha = $("hist-a"), hb = $("hist-b");
  sel.innerHTML = ""; ha.innerHTML = ""; hb.innerHTML = "";
  for (const r of d.runs) {
    for (const s of [sel, ha, hb]) {
      const o = document.createElement("option");
      o.value = r.ts;
      o.textContent = `${r.ts} (${r.n_ranked} resolvers${r.incomplete ? ", " + r.incomplete + " flagged" : ""})`;
      s.appendChild(o);
    }
  }
  if (d.runs.length) {
    sel.value = state.selectedRun || d.runs[0].ts;
    ha.value = d.runs[Math.min(1, d.runs.length - 1)].ts;
    hb.value = d.runs[0].ts;
  }
  $("run-badge").textContent = d.runs.length + " runs stored";
}

async function loadRun(ts) {
  if (!ts) return;
  const r = await (await fetch("/api/runs/" + ts)).json();
  if (r.error) return;
  renderRankTable(r);
  renderProviderTable(r);
}

function renderRankTable(r) {
  const tb = $("rank-table").querySelector("tbody");
  tb.innerHTML = "";
  const frag = document.createDocumentFragment();
  r.ranked.forEach((row, i) => {
    const tr = document.createElement("tr");
    const flagged = (row.to_c || 0) + (row.bad_c || 0) > 0;
    tr.innerHTML =
      `<td>${i + 1}</td>` +
      `<td class="ip">${row.ip}</td>` +
      `<td>${row.u_med}</td><td>${row.c_med}</td>` +
      `<td class="${row.to_u ? "warn" : ""}">${row.to_u}</td>` +
      `<td class="${row.to_c ? "warn" : ""}">${row.to_c}</td>` +
      `<td class="${row.bad_c ? "warn" : ""}">${row.bad_c || 0}</td>` +
      `<td class="provider" title="${row.ptr || ""}">${row.ptr || "?"}</td>`;
    frag.appendChild(tr);
  });
  tb.appendChild(frag);
}

function renderProviderTable(r) {
  const by = new Map();
  for (const row of r.ranked) {
    const cat = row.category || "Other";
    if (!by.has(cat)) by.set(cat, { n: 0, best: Infinity, mids: [] });
    const e = by.get(cat);
    e.n++;
    if (row.u_med < e.best) e.best = row.u_med;
    e.mids.push(row.u_med);
  }
  const tb = $("provider-table").querySelector("tbody");
  tb.innerHTML = "";
  const rows = [...by.entries()].sort((a, b) => a[1].best - b[1].best);
  for (const [cat, e] of rows) {
    e.mids.sort((a, b) => a - b);
    const med = e.mids[Math.floor(e.mids.length / 2)];
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${cat}</td><td>${e.n}</td><td>${e.best === Infinity ? "—" : e.best}</td><td>${med}</td>`;
    tb.appendChild(tr);
  }
}

$("run-select").addEventListener("change", (e) => loadRun(e.target.value));
$("btn-compare").addEventListener("click", async () => {
  const a = $("hist-a").value, b = $("hist-b").value;
  if (!a || !b) return;
  const d = await (await fetch(`/api/compare/${a}/${b}`)).json();
  if (d.error) { $("compare-result").textContent = d.error; return; }
  // denominator = the smaller run's compared population (top-50 window),
  // not a hardcoded 50: two 14-resolver runs share 14/14, not 14/50
  const denom = Math.min(50, d.n_a ?? 50, d.n_b ?? 50);
  let html = `<p>top-${denom} overlap: <b>${d.overlap}/${denom}</b></p>`;
  html += "<table><thead><tr><th>Ref</th><th>A rank/u_med</th><th>B rank/u_med</th></tr></thead><tbody>";
  for (const [ip, v] of Object.entries(d.refs || {})) {
    html += `<tr><td class="ip">${ip}</td><td>#${v.a.rank}, ${v.a.u_med}ms</td><td>#${v.b.rank}, ${v.b.u_med}ms</td></tr>`;
  }
  html += "</tbody></table>";
  $("compare-result").innerHTML = html;
});

/* ---------- viz: WASM interop ---------- */
const CAT_COLORS = {
  Akamai: [255, 106, 61],
  Cloudflare: [245, 158, 11],
  Google: [80, 180, 255],
  Quad9: [122, 162, 247],
  OpenDNS: [187, 134, 252],
  ControlD: [94, 234, 212],
  AdGuard: [255, 99, 132],
  CleanBrowsing: [79, 209, 197],
  Verisign: [129, 140, 248],
  MagicDNS: [52, 211, 153],
  Other: [110, 120, 135],
};

async function initViz() {
  const resp = await fetch("/viz.wasm");
  const bytes = await resp.arrayBuffer();
  const canvas = $("viz-canvas");
  const ctx = canvas.getContext("2d", { alpha: false });

  const env = {
    viz_set_fillstyle: (r, g, b, a) => {
      ctx.fillStyle = `rgba(${r},${g},${b},${a.toFixed(3)})`;
    },
    viz_fill_rect: (x, y, w, h) => ctx.fillRect(x, y, w, h),
  };
  const { instance } = await WebAssembly.instantiate(bytes, { env });
  const ex = instance.exports;
  const nCats = Object.keys(CAT_COLORS).length;
  // palette upload: wasm scratch is shared; upload palette FIRST (before the
  // large layout/ingest scratch regions overwrite it)
  const catNames = Object.keys(CAT_COLORS);
  const palBuf = new Uint8Array(nCats * 3);
  catNames.forEach((n, i) => {
    const c = CAT_COLORS[n];
    palBuf[i * 3] = c[0]; palBuf[i * 3 + 1] = c[1]; palBuf[i * 3 + 2] = c[2];
  });
  if (ex.viz_palette_ptr) {
    const pptr = ex.viz_palette_ptr();
    const memView = new Uint8Array(ex.memory.buffer, pptr, nCats * 3);
    memView.set(palBuf);
    ex.viz_set_palette(pptr, nCats);
  }
  state.wasm = { ex, canvas, ctx };
  window.__viz = state.wasm;  // debug hook
  resizeViz();
  new ResizeObserver(resizeViz).observe(canvas);
  requestAnimationFrame(tick);
}

let lastTs = 0;
function tick(ts) {
  const dt = lastTs ? Math.min(ts - lastTs, 50) : 16;
  lastTs = ts;
  try {
    const { ex } = state.wasm;
    const m = ex.viz_tick(dt);
    if (m || ex.viz_is_dirty()) {
      ex.viz_render();
      ex.viz_clear_dirty();
    }
  } catch (e) {
    console.error("viz tick failed:", e);
  }
  requestAnimationFrame(tick);
}

function resizeViz() {
  const { ex, canvas } = state.wasm;
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ex.viz_resize(canvas.width, canvas.height);
}

/* ingest dataset into wasm: fetch points once, layout spiral, bulk-copy */
async function refreshPoints() {
  const d = await (await fetch("/api/points")).json();
  state.points = d;
  ingestPoints();
  renderLegend(d.cat_names);
}

function ingestPoints() {
  const { ex } = state.wasm;
  const pts = state.points.points;
  const n = pts.length;
  const nCats = state.points.cat_names.length;
  const stride = 5; // x, y, cat, status, lat
  // CRITICAL: allocate ALL scratch regions BEFORE creating any typed-array views.
  // A later viz_scratch_alloc can grow wasm memory, which detaches the old
  // ArrayBuffer and turns existing views into dead (write=NaN) objects.
  const layoutBytes = n * 2 * 4;
  const layoutPtr = ex.viz_scratch_alloc(layoutBytes);
  const ingestBytes = n * stride * 4;
  const ingestPtr = ex.viz_scratch_alloc(ingestBytes);
  const changedPtr = ex.viz_scratch_alloc(4);
  // views created only after every allocation
  const layoutView = new Float32Array(ex.memory.buffer, layoutPtr, n * 2);
  const ingestView = new Float32Array(ex.memory.buffer, ingestPtr, n * stride);
  ex.viz_layout_spiral(layoutPtr, n);
  for (let i = 0; i < n; i++) {
    const [cat, lat, status] = pts[i];
    ingestView[i * stride] = layoutView[i * 2];
    ingestView[i * stride + 1] = layoutView[i * 2 + 1];
    ingestView[i * stride + 2] = cat >= nCats ? nCats - 1 : cat;
    ingestView[i * stride + 3] = status;
    ingestView[i * stride + 4] = lat;
  }
  ex.viz_ingest(ingestPtr, n, changedPtr);
  const changed = new Int32Array(ex.memory.buffer, changedPtr, 1)[0];
  // scratch fully consumed by viz_ingest: rewind the arena so repeated
  // re-ingests (live updates, run refresh) reuse the same blocks instead of
  // growing wasm memory every call
  ex.viz_scratch_reset();
  $("viz-meta").textContent = `${n.toLocaleString()} points · ${changed.toLocaleString()} changed`;
  $("viz-meta").dataset.count = n;
}

/* live ticks during a run: individual server results arrive out of band */
const ipIndex = new Map();
function buildIpIndex() {
  // master-ipv4 order == /api/points order; fetch master list once via points meta?
  // The points payload doesn't carry IPs (62k strings would bloat); instead the
  // server includes idx in server_done events. Fallback: match by ip via index map
  // built from /api/points-meta.
  fetch("/api/points-meta").then((r) => r.json()).then((d) => {
    d.ips.forEach((ip, i) => ipIndex.set(ip, i));
  }).catch(() => { });
}

let pendingVizUpdates = [];
function vizPointTick(d) {
  if (d.idx === undefined && ipIndex.has(d.ip)) d.idx = ipIndex.get(d.ip);
  if (d.idx === undefined) return;
  pendingVizUpdates.push(d);
  if (pendingVizUpdates.length >= 8) flushVizUpdates();
}

function flushVizUpdates() {
  if (!pendingVizUpdates.length || !state.wasm) return;
  const { ex } = state.wasm;
  const pts = state.points.points;
  const batch = pendingVizUpdates.splice(0, pendingVizUpdates.length);
  for (const d of batch) {
    const i = d.idx;
    if (i === undefined || i >= pts.length) continue;
    if (d.dead) { pts[i][2] = 2; }
    else {
      pts[i][1] = d.u_med || pts[i][1];
      pts[i][2] = 1;
    }
  }
  ingestPoints();
}

/* liveness streaming: fade in/out en masse */
function vizLivenessTick(done, alive) {
  // done candidates probed so far; approximate: first `done` indices of the
  // shuffled liveness order are unknown to us — skip per-point, the final
  // /api/points refresh after discovery handles exact state.
}

function vizFlashRun() {
  flushVizUpdates(); // deliver stragglers below the 8-event threshold too
  ingestPoints(); // refresh from latest datasets (includes completed run data)
}

function renderLegend(catNames) {
  const el = $("viz-legend");
  el.innerHTML = "";
  for (const name of catNames) {
    const c = CAT_COLORS[name] || CAT_COLORS.Other;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `<span class="swatch" style="background:rgb(${c[0]},${c[1]},${c[2]})"></span>${name}`;
    el.appendChild(chip);
  }
}

/* ---------- boot ---------- */
(async function boot() {
  connectEvents();
  await initViz();
  await refreshPoints();
  buildIpIndex();
  await loadRunList();
  if (state.runs.length) {
    state.selectedRun = state.runs[0].ts;
    $("run-select").value = state.selectedRun;
    await loadRun(state.selectedRun);
  }
  const s = await (await fetch("/api/state")).json();
  onState(s);
  setStatus("ready");
})();
