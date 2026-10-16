/* DNSBench frontend: minimal JS — SSE, controls, SVG/HTML charts.
   No frameworks, no wasm. Charts answer: fastest? reliable? consistent? */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  running: false,
  pendingConfirm: false,
  runs: [],
  selectedRun: "",
  run: null,             // payload of /api/runs/<ts> for the selected run
  selected: null,        // selected resolver ip
  coverage: null,
  providerFilter: null,  // null = all
  liveDone: 0,           // server_done events for the running job
  liveTotal: 0,
};

const REFS = ["1.0.0.1", "8.8.8.8", "9.9.9.9", "208.67.222.222"];

/* provider colors (kept from the field palette) */
const CAT_COLORS = {
  Akamai: "#ff6a3d", Cloudflare: "#f59e0b", Google: "#50b4ff",
  Quad9: "#7aa2f7", OpenDNS: "#bb86fc", ControlD: "#5eead4",
  AdGuard: "#ff6384", CleanBrowsing: "#4fd1c5", Verisign: "#818cf8",
  MagicDNS: "#34d399", Other: "#6e7887",
};
const catColor = (c) => {
  if (CAT_COLORS[c]) return CAT_COLORS[c];
  if (!c) return CAT_COLORS.Other;
  // deterministic distinguishable hue for providers outside the catalog
  let h = 0;
  for (const ch of c) h = (h * 31 + ch.charCodeAt(0)) % 997;
  return `hsl(${Math.round(h * 137.508) % 360}, 55%, 62%)`;
};

const fmtMs = (v) => (v == null ? "—" : `${v} ms`);

/* ---------- SSE ---------- */
function connectEvents() {
  const es = new EventSource("/events");
  const map = {
    state: onState, stage: onStage, progress: onProgress,
    server_done: onServerDone, pool_updated: onPoolUpdated,
    run_complete: onRunComplete, job_error: onJobError,
    job_done: onJobDone, cancelled: onJobDone,
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
    showProgress();
    $("progress-label").textContent = d.stage === "discovery"
      ? "screening (liveness + probes)" : `deep bench ${d.n_servers || ""}`;
    if (d.stage === "deepbench") { state.liveDone = 0; state.liveTotal = d.n_servers || 0; }
  }
}

function onProgress(d) {
  const pct = d.total ? Math.round((d.done / d.total) * 100) : 0;
  $("progress-bar").style.setProperty("--w", pct + "%");
  let label = `${d.stage}: ${d.done}/${d.total}`;
  if (d.alive !== null && d.alive !== undefined) label += ` alive ${d.alive}`;
  $("progress-label").textContent = label;
}

function onServerDone(d) {
  state.liveDone++;
  renderLiveMeta();
}

function renderLiveMeta() {
  if (!state.running || !state.liveTotal) return;
  const el = $("run-meta");
  if (!el) return;
  const live = el.querySelector(".live-line");
  if (live) live.textContent = `measuring: ${state.liveDone}/${state.liveTotal} resolvers done`;
}

function onPoolUpdated() { loadCoverage(); }

function onRunComplete(d) {
  loadRunList().then(() => {
    $("run-select").value = d.run_dir;
    loadRun(d.run_dir);
  });
  $("stage-badge").textContent = "idle";
  $("stage-badge").className = "stage-badge idle";
  hideProgress();
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
  renderLiveMetaClear();
}

function renderLiveMetaClear() {
  const live = $("run-meta")?.querySelector(".live-line");
  if (live) live.textContent = "";
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
      state.pendingConfirm = true;
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
  state.pendingConfirm = false;
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

function showProgress() {
  $("progress-wrap").hidden = false;
  $("progress-bar").style.setProperty("--w", "0%");
}
function hideProgress() { $("progress-wrap").hidden = true; }

/* ---------- run list + selection ---------- */
async function loadRunList() {
  const d = await (await fetch("/api/runs")).json();
  state.runs = d.runs;
  const sel = $("run-select"), ha = $("hist-a"), hb = $("hist-b");
  sel.innerHTML = ""; ha.innerHTML = ""; hb.innerHTML = "";
  for (const r of d.runs) {
    for (const s of [sel, ha, hb]) {
      const o = document.createElement("option");
      o.value = r.ts;
      o.textContent = `${r.ts} (${r.n_ranked}r${r.incomplete ? ", " + r.incomplete + "⚠" : ""})`;
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
  state.selectedRun = ts;
  state.run = r;
  if (state.selected && !r.ranked.some((x) => x.ip === state.selected)) {
    state.selected = null;  // selection not present in this run
  }
  renderRun();
  if (state.selected) selectResolver(state.selected, false);
  else renderDetail();
}

function renderRunMeta() {
  const r = state.run;
  if (!r) { $("run-meta").textContent = ""; return; }
  const inc = (r.incomplete_cached || []).length;
  const refs = 4;
  const finalists = Math.max(0, (r.n_deepbenched || 0) - refs);
  $("run-meta").innerHTML =
    `<span>measured ${esc(r.generated || "?")} UTC</span>` +
    `<span>requested ${finalists} + ${refs} refs</span>` +
    `<span>benched ${r.n_deepbenched}</span>` +
    `<span>ranked ${r.n_ranked}</span>` +
    (inc ? `<span class="warn">incomplete cached samples: ${inc}</span>` : "") +
    `<span class="live-line"></span>`;
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);

/* ---------- rankings: bars + table + refs ---------- */
function niceMax(v) {
  if (v <= 0) return 10;
  const steps = [10, 20, 25, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300, 400, 500];
  return steps.find((s) => s >= v) || Math.ceil(v / 100) * 100;
}

function flagsFor(row, incompleteSet) {
  const to = (row.to_u || 0) + (row.to_c || 0);
  const bad = row.bad_c || 0;
  const parts = [];
  if (to) parts.push(`<span class="flag warn" title="timeouts (no answer in time): ${to}">⏱${to}</span>`);
  if (bad) parts.push(`<span class="flag warn" title="invalid cached answers (no usable address): ${bad}">✗${bad}</span>`);
  if (incompleteSet && incompleteSet.has(row.ip)) parts.push(`<span class="flag bad" title="incomplete cached samples — medians less reliable">⚠</span>`);
  return parts.join("");
}

function barRow(row, rank, incompleteSet, isRef) {
  const div = document.createElement("div");
  div.className = "bar-row" + (state.selected === row.ip ? " selected" : "") + (isRef ? " ref" : "");
  div.dataset.ip = row.ip;
  div.setAttribute("role", "option");
  div.tabIndex = 0;
  const u = row.u_med, c = row.c_med;
  div.setAttribute("aria-label",
    `${isRef ? "reference " : "rank " + rank + ", "} ${row.ip}: uncached ${u == null ? "missing" : u + " ms"}, cached ${c == null ? "missing" : c + " ms"}`);
  div.innerHTML =
    `<span class="bar-rank">${isRef ? "ref" : rank}</span>` +
    `<span class="bar-ip" title="${esc(row.ptr || "")}">${row.ip}</span>` +
    `<div class="bar-track">` +
    `<div class="bar u" style="width:${u == null ? 0 : (u / state.barMax * 100).toFixed(2)}%" title="uncached median: ${u == null ? "no valid samples" : u + " ms"}"></div>` +
    `<div class="bar c" style="width:${c == null ? 0 : (c / state.barMax * 100).toFixed(2)}%" title="cached median: ${c == null ? "no valid samples" : c + " ms"}"></div></div>` +
    `<span class="bar-vals"><b>${u == null ? "—" : u.toFixed(1)}</b><i>${c == null ? "—" : c.toFixed(1)}</i></span>` +
    `<span class="bar-flags">${flagsFor(row, incompleteSet)}</span>`;
  div.addEventListener("click", () => selectResolver(row.ip));
  div.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectResolver(row.ip); }
  });
  return div;
}

function renderBars() {
  const r = state.run;
  const incSet = new Set(r.incomplete_cached || []);
  const top = r.ranked.slice(0, 20);
  const shown = [...top, ...r.ranked.filter((x) => REFS.includes(x.ip))];
  const vals = shown.flatMap((x) => [x.u_med, x.c_med]).filter((v) => v != null);
  state.barMax = niceMax(Math.max(...vals, 1));
  const chart = $("bar-chart");
  chart.innerHTML = "";
  for (let i = 0; i < top.length; i++) chart.appendChild(barRow(top[i], i + 1, incSet, false));
  if (!top.length) chart.innerHTML = '<p class="note">no ranked resolvers in this run</p>';
  // axis
  const axis = $("bar-axis");
  const ticks = ticksFor(state.barMax);
  axis.innerHTML = ticks.map((t) => `<span style="left:${(t / state.barMax * 100).toFixed(2)}%">${t}</span>`).join("") + '<span class="axis-unit">ms</span>';
  // references strip
  const refsRows = $("refs-rows");
  refsRows.innerHTML = "";
  const refRows = REFS.map((ip) => r.ranked.find((x) => x.ip === ip)).filter(Boolean);
  const rankOf = new Map(r.ranked.map((x, i) => [x.ip, i + 1]));
  for (const row of refRows) refsRows.appendChild(barRow(row, rankOf.get(row.ip), incSet, true));
  if (!refRows.length) refsRows.innerHTML = '<p class="note">no reference measurements in this run</p>';
}

function ticksFor(max) {
  const t = [];
  const step = max / 4;
  for (let i = 0; i <= 4; i++) t.push(Math.round(i * step * 10) / 10);
  return t;
}

function renderTable() {
  const r = state.run;
  const tb = $("rank-table").querySelector("tbody");
  tb.innerHTML = "";
  const frag = document.createDocumentFragment();
  const incSet = new Set(r.incomplete_cached || []);
  const rankOf = new Map(r.ranked.map((x, i) => [x.ip, i + 1]));
  r.ranked.forEach((row, i) => {
    const tr = document.createElement("tr");
    tr.dataset.ip = row.ip;
    tr.className = state.selected === row.ip ? "selected" : "";
    tr.tabIndex = 0;
    tr.setAttribute("aria-label", `rank ${i + 1}, ${row.ip}`);
    const isRef = REFS.includes(row.ip);
    tr.innerHTML =
      `<td>${i + 1}${isRef ? ' <span class="ref-badge">ref</span>' : ""}${incSet.has(row.ip) ? ' <span class="flag bad" title="incomplete cached samples">⚠</span>' : ""}</td>` +
      `<td class="ip">${row.ip}</td>` +
      `<td>${row.u_med == null ? "—" : row.u_med.toFixed(1)}</td><td>${row.c_med == null ? "—" : row.c_med.toFixed(1)}</td>` +
      `<td class="${row.to_u ? "warn" : ""}">${row.to_u || 0}</td>` +
      `<td class="${row.to_c ? "warn" : ""}">${row.to_c || 0}</td>` +
      `<td class="${row.bad_c ? "warn" : ""}">${row.bad_c || 0}</td>` +
      `<td class="provider" title="${esc(row.ptr || "")}">${esc(row.category || row.ptr || "?")}</td>`;
    tr.addEventListener("click", () => selectResolver(row.ip));
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectResolver(row.ip); }
    });
    frag.appendChild(tr);
  });
  tb.appendChild(frag);
}

function renderProviderTable() {
  const r = state.run;
  const by = new Map();
  for (const row of r.ranked) {
    const cat = row.category || "Other";
    if (!by.has(cat)) by.set(cat, { n: 0, best: Infinity, mids: [] });
    const e = by.get(cat);
    e.n++;
    if (row.u_med != null && row.u_med < e.best) e.best = row.u_med;
    if (row.u_med != null) e.mids.push(row.u_med);
  }
  const tb = $("provider-table").querySelector("tbody");
  tb.innerHTML = "";
  for (const [cat, e] of [...by.entries()].sort((a, b) => a[1].best - b[1].best)) {
    e.mids.sort((a, b) => a - b);
    const med = e.mids.length ? e.mids[Math.floor(e.mids.length / 2)] : "—";
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><span class="swatch" style="background:${catColor(cat)}"></span>${esc(cat)}</td>` +
      `<td>${e.n}</td><td>${e.best === Infinity ? "—" : e.best}</td><td>${med}</td>`;
    tb.appendChild(tr);
  }
}

function renderRun() {
  renderRunMeta();
  renderBars();
  renderTable();
  renderProviderTable();
  renderExplore();
}

/* ---------- selection + detail ---------- */
async function selectResolver(ip, scroll = true) {
  state.selected = ip;
  renderBarsSelected();
  renderExplore();
  await renderDetail();
  renderAcross();
  if (scroll) $("detail").scrollIntoView({ block: "nearest" });
}

function renderBarsSelected() {
  document.querySelectorAll(".bar-row").forEach((el) => {
    el.classList.toggle("selected", el.dataset.ip === state.selected);
  });
  document.querySelectorAll("#rank-table tbody tr").forEach((el) => {
    el.classList.toggle("selected", el.dataset.ip === state.selected);
  });
}

const METRIC_HELP = [
  ["u_med", "Uncached median", "typical time to resolve a name the resolver has never seen — the honest 'first ask' speed"],
  ["c_med", "Cached median", "typical time when the resolver already has the answer in its cache"],
  ["u_p90", "Uncached p90", "90% of uncached lookups finished at or below this time — tail behavior"],
  ["to", "Timeouts", "queries with no answer in time (counted separately, never averaged in)"],
  ["bad_c", "Invalid cached", "answers that arrived but carried no usable address — a resolver-quality problem, not latency"],
  ["samples", "Samples", "probes actually recorded in this run's per-query audit log; medians come only from valid ones"],
  ["incomplete", "Incomplete", "fewer valid cached samples than the expected 50 — treat this resolver's cached median with caution"],
];

function helpList() {
  return `<dl class="metric-help">${METRIC_HELP.map(([k, t, d]) =>
    `<dt>${t}</dt><dd>${d}</dd>`).join("")}</dl>`;
}

async function renderDetail() {
  const el = $("detail-body");
  const ip = state.selected;
  if (!ip || !state.run) {
    el.innerHTML = '<p class="note">Select a resolver to see its measurements.</p>';
    return;
  }
  const row = state.run.ranked.find((x) => x.ip === ip);
  if (!row) { el.innerHTML = '<p class="note">not present in this run</p>'; return; }
  const incSet = new Set(state.run.incomplete_cached || []);
  const incomplete = incSet.has(ip);
  el.innerHTML = "<p class=\"note\">loading samples…</p>";
  let q = null;
  try {
    q = await (await fetch(`/api/runs/${state.selectedRun}/queries/${ip}`)).json();
  } catch { /* leave q null */ }
  const uOk = q && !q.error ? q.uncached : null;
  const cOk = q && !q.error ? q.cached : null;
  el.innerHTML =
    `<div class="detail-head"><span class="detail-ip">${ip}</span>` +
    `<span class="detail-cat"><span class="swatch" style="background:${catColor(row.category)}"></span>${esc(row.category || "?")}</span>` +
    `<span class="detail-ptr muted" title="${esc(row.ptr || "")}">${esc(row.ptr || "no PTR label")}</span>` +
    `${REFS.includes(ip) ? '<span class="ref-badge">mandatory reference</span>' : ""}` +
    `${incomplete ? '<span class="flag bad">⚠ incomplete cached samples</span>' : ""}</div>` +
    `<table class="detail-metrics"><tbody>` +
    `<tr><td>Uncached median</td><td>${fmtMs(row.u_med)}</td></tr>` +
    `<tr><td>Cached median</td><td>${fmtMs(row.c_med)}</td></tr>` +
    `<tr><td>Uncached p90</td><td>${fmtMs(row.u_p90)}</td></tr>` +
    `<tr><td>Timeouts (uncached / cached)</td><td>${row.to_u || 0} / ${row.to_c || 0}</td></tr>` +
    `<tr><td>Invalid cached answers</td><td>${row.bad_c || 0}</td></tr>` +
    (uOk ? `<tr><td>Uncached samples recorded</td><td>${uOk.ok.length} valid + ${uOk.timeouts} timed out</td></tr>` : "") +
    (cOk ? `<tr><td>Cached samples recorded</td><td>${cOk.ok.length} valid + ${cOk.timeouts} timed out + ${cOk.invalid} invalid</td></tr>` : "") +
    `</tbody></table>` +
    (q && !q.error ? distStrip("Uncached samples", uOk, distMax(uOk, cOk))
      + distStrip("Cached samples", cOk, distMax(uOk, cOk))
      : '<p class="note bad-note">no per-query sample log for this run — distribution unavailable, medians above are as stored</p>') +
    `<details class="help"><summary>What these metrics mean</summary>${helpList()}</details>`;
}

function distMax(a, b) {
  // one shared scale for both strips so they're comparable
  const all = [...(a?.ok || []), ...(b?.ok || [])];
  return Math.max(...all, 1);
}

function distStrip(label, b, maxMs) {
  if (!b) return "";
  const vals = b.ok.filter((v) => v != null);
  // histogram bins: 50 near-identical samples would overdraw as one blob
  const BINS = 24;
  const counts = new Array(BINS).fill(0);
  for (const v of vals) {
    const bi = Math.min(BINS - 1, Math.max(0, Math.floor(v / maxMs * BINS)));
    counts[bi]++;
  }
  const peak = Math.max(...counts, 1);
  const bars = counts.map((c, i) => c ? `<i class="bin" style="left:${(i / BINS * 100).toFixed(2)}%;width:${(100 / BINS - 0.6).toFixed(2)}%;height:${Math.max(15, c / peak * 100).toFixed(0)}%" title="${c} sample${c > 1 ? "s" : ""} in ${(i * maxMs / BINS).toFixed(0)}–${((i + 1) * maxMs / BINS).toFixed(0)} ms"></i>` : "").join("");
  const extras = [];
  if (b.timeouts) extras.push(`<span class="flag warn">⏱ ${b.timeouts} timed out</span>`);
  if (b.invalid) extras.push(`<span class="flag warn">✗ ${b.invalid} invalid</span>`);
  return `<div class="dist"><div class="dist-label">${label} ` +
    `<span class="muted">(${vals.length} valid)</span></div>` +
    `<div class="dist-track">${bars}</div>` +
    `<div class="dist-scale"><span>0 ms</span><span>${maxMs} ms</span></div>` +
    `<div class="dist-extra">${extras.join(" ") || '<span class="muted">all ' + vals.length + ' samples valid</span>'}</div></div>`;
}

/* ---------- explore scatter ---------- */
function renderExplore() {
  const r = state.run;
  const svg = $("explore-svg");
  const note = $("explore-note");
  if (!r) { svg.innerHTML = ""; note.textContent = ""; return; }
  const W = 640, H = 400, M = { l: 46, r: 12, t: 12, b: 34 };
  const pts = r.ranked.filter((x) => x.u_med != null && x.c_med != null);
  const cats = [...new Set(r.ranked.map((x) => x.category || "Other"))].sort();
  // filters
  const fl = $("explore-filters");
  fl.innerHTML = "";
  const mkChip = (label, cat) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + ((state.providerFilter == null || state.providerFilter === cat) ? " on" : "");
    b.innerHTML = `<span class="swatch" style="background:${catColor(cat)}"></span>${label}`;
    b.setAttribute("aria-pressed", state.providerFilter === cat ? "true" : "false");
    b.addEventListener("click", () => {
      state.providerFilter = (state.providerFilter === cat || cat == null) ? null : cat;
      renderExplore();
    });
    return b;
  };
  fl.appendChild(mkChip("All", null));
  for (const c of cats) fl.appendChild(mkChip(c, c));
  // scale: linear from 0
  const allVals = pts.flatMap((x) => [x.u_med, x.c_med]);
  const maxMs = niceMax(Math.max(...allVals, 1));
  const x = (v) => M.l + (v / maxMs) * (W - M.l - M.r);
  const y = (v) => H - M.b - (v / maxMs) * (H - M.t - M.b);
  const filtered = state.providerFilter ? pts.filter((p) => p.category === state.providerFilter) : pts;
  let s = "";
  // gridlines + ticks
  for (const t of ticksFor(maxMs)) {
    s += `<line x1="${x(t)}" y1="${M.t}" x2="${x(t)}" y2="${H - M.b}" class="grid"/>`;
    s += `<line x1="${M.l}" y1="${y(t)}" x2="${W - M.r}" y2="${y(t)}" class="grid"/>`;
    s += `<text x="${x(t)}" y="${H - M.b + 14}" class="tick" text-anchor="middle">${t}</text>`;
    s += `<text x="${M.l - 6}" y="${y(t) + 4}" class="tick" text-anchor="end">${t}</text>`;
  }
  s += `<text x="${M.l + (W - M.l - M.r) / 2}" y="${H - 4}" class="axis-label" text-anchor="middle">uncached median (ms) →</text>`;
  s += `<text x="12" y="${M.t + (H - M.t - M.b) / 2}" class="axis-label" transform="rotate(-90 12 ${M.t + (H - M.t - M.b) / 2})" text-anchor="middle">cached median (ms) →</text>`;
  s += `<text x="${W - M.r - 4}" y="${y(0) - 6}" class="axis-note" text-anchor="end">faster ↙</text>`;
  for (const p of filtered) {
    const col = catColor(p.category);
    s += `<circle cx="${x(p.u_med).toFixed(1)}" cy="${y(p.c_med).toFixed(1)}" r="4" fill="${col}" ` +
      `tabindex="0" role="button" aria-label="${p.ip}, uncached ${p.u_med} ms, cached ${p.c_med} ms, ${esc(p.category || "Other")}${state.selected === p.ip ? ', selected' : ''}" ` +
      `class="pt${state.selected === p.ip ? " sel" : ""}"><title>${p.ip} — uncached ${p.u_med} ms, cached ${p.c_med} ms (${esc(p.category || "Other")})</title></circle>`;
  }
  svg.innerHTML = s;
  svg.querySelectorAll(".pt").forEach((el) => {
    const ip = el.getAttribute("aria-label").split(",")[0];
    el.addEventListener("click", () => selectResolver(ip));
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectResolver(ip); }
    });
  });
  const dead = (r.n_deepbenched || 0) - (r.n_ranked || 0);
  const hidden = pts.length - filtered.length;
  const noMedian = r.ranked.filter((x) => x.u_med == null || x.c_med == null).length;
  note.textContent =
    `${pts.length} plottable of ${r.n_ranked} ranked` +
    (noMedian ? ` · ${noMedian} ranked without a plottable median (shown as bars only)` : "") +
    (dead ? ` · ${dead} benched endpoints not rankable (dead or no valid answers)` : "") +
    (hidden ? ` · ${hidden} hidden by filter` : "");
}

/* ---------- across-runs history ---------- */
async function renderAcross() {
  const svg = $("across-svg");
  if (!state.selected) { svg.innerHTML = '<text x="320" y="150" class="axis-note" text-anchor="middle">select a resolver</text>'; return; }
  let d;
  try {
    d = await (await fetch(`/api/history?ip=${state.selected}`)).json();
  } catch { svg.innerHTML = ""; return; }
  if (d.error) { svg.innerHTML = `<text x="320" y="150" class="axis-note" text-anchor="middle">${esc(d.error)}</text>`; return; }
  const W = 640, H = 300, M = { l: 46, r: 12, t: 14, b: 40 };
  const runs = d.runs; // chronological
  const meVals = runs.map((r) => (r.me && r.me.u_med != null) ? r.me.u_med : null);
  const refVals = Object.entries(d.refs).map(([ip, arr]) => ({ ip, vals: arr.map((x) => x ? x.u_med : null) }));
  const all = [...meVals, ...refVals.flatMap((s) => s.vals)].filter((v) => v != null);
  const maxMs = niceMax(Math.max(...all, 1));
  const n = runs.length;
  const px = (i) => n === 1 ? (M.l + (W - M.l - M.r) / 2) : M.l + (i / (n - 1)) * (W - M.l - M.r);
  const py = (v) => H - M.b - (v / maxMs) * (H - M.t - M.b);
  let s = "";
  for (const t of ticksFor(maxMs)) {
    s += `<line x1="${M.l}" y1="${py(t)}" x2="${W - M.r}" y2="${py(t)}" class="grid"/>`;
    s += `<text x="${M.l - 6}" y="${py(t) + 4}" class="tick" text-anchor="end">${t}</text>`;
  }
  // run labels: first, last, and a few between
  const labelEvery = Math.max(1, Math.ceil(n / 6));
  runs.forEach((r, i) => {
    if (i % labelEvery === 0 || i === n - 1) {
      s += `<text x="${px(i)}" y="${H - M.b + 14}" class="tick" text-anchor="end" transform="rotate(-38 ${px(i)} ${H - M.b + 14})">${r.ts.slice(9, 15)}</text>`;
    }
  });
  s += `<text x="${M.l + (W - M.l - M.r) / 2}" y="${H - 4}" class="axis-label" text-anchor="middle">stored runs (oldest → newest)</text>`;
  s += `<text x="12" y="${M.t + (H - M.t - M.b) / 2}" class="axis-label" transform="rotate(-90 12 ${M.t + (H - M.t - M.b) / 2})" text-anchor="middle">uncached median (ms)</text>`;
  // series: segments only between consecutive non-null points (gaps are breaks)
  const series = [{ ip: state.selected, vals: meVals, cls: "me" },
    ...refVals.map((r) => ({ ...r, cls: "ref" }))];
  for (const ser of series) {
    let prev = null;
    for (let i = 0; i < runs.length; i++) {
      const v = ser.vals[i];
      if (v == null) { prev = null; continue; }
      if (prev != null) {
        s += `<line x1="${px(prev)}" y1="${py(ser.vals[prev])}" x2="${px(i)}" y2="${py(v)}" class="ln ${ser.cls}"/>`;
      }
      s += `<circle cx="${px(i)}" cy="${py(v)}" r="${ser.cls === "me" ? 3.5 : 2.5}" class="pt ${ser.cls}" ` +
        `tabindex="0" aria-label="${runs[i].ts}: ${v} ms, rank ${runs[i].rank ?? "—"} of ${runs[i].n_ranked}">` +
        `<title>${runs[i].ts} — ${ser.ip === state.selected ? "selected" : "ref " + ser.ip}: ${v} ms (rank ${runs[i].rank ?? "—"} of ${runs[i].n_ranked})</title></circle>`;
      prev = i;
    }
  }
  // legend
  s += `<text x="${M.l}" y="${M.t + 2}" class="lg me">■ selected</text>`;
  s += `<text x="${M.l + 70}" y="${M.t + 2}" class="lg ref">■ references (1.0.0.1 · 8.8.8.8 · 9.9.9.9 · 208.67.222.222)</text>`;
  svg.innerHTML = s;
  svg.querySelectorAll(".pt").forEach((el) => { el.setAttribute("role", "button"); });
}

/* ---------- coverage ---------- */
async function loadCoverage() {
  try {
    state.coverage = await (await fetch("/api/coverage")).json();
  } catch { return; }
  const c = state.coverage;
  $("coverage-stats").innerHTML =
    `<span><b>${c.total.toLocaleString()}</b> candidates</span>` +
    `<span class="ok">${(c.alive || 0).toLocaleString()} alive</span>` +
    `<span>${(c.dead || 0).toLocaleString()} dead</span>` +
    `<span class="warn">${c.hijack || 0} hijack-flagged</span>` +
    (c.pool_eligible ? `<span>pool ${c.pool_eligible.toLocaleString()} eligible</span>` : "") +
    (c.pool_generated ? `<span class="muted">screened ${c.pool_generated}</span>` : "");
}

/* ---------- compare (kept, compact) ---------- */
$("run-select").addEventListener("change", (e) => loadRun(e.target.value));
$("btn-compare").addEventListener("click", async () => {
  const a = $("hist-a").value, b = $("hist-b").value;
  if (!a || !b) return;
  const d = await (await fetch(`/api/compare/${a}/${b}`)).json();
  if (d.error) { $("compare-result").textContent = d.error; return; }
  const denom = Math.min(50, d.n_a ?? 50, d.n_b ?? 50);
  let html = `<p>top-${denom} overlap: <b>${d.overlap}/${denom}</b></p>`;
  html += "<table><thead><tr><th>Ref</th><th>A rank/u_med</th><th>B rank/u_med</th></tr></thead><tbody>";
  for (const [ip, v] of Object.entries(d.refs || {})) {
    html += `<tr><td class="ip">${ip}</td><td>#${v.a.rank}, ${v.a.u_med}ms</td><td>#${v.b.rank}, ${v.b.u_med}ms</td></tr>`;
  }
  html += "</tbody></table>";
  $("compare-result").innerHTML = html;
});

/* ---------- boot ---------- */
(async function boot() {
  connectEvents();
  await loadCoverage();
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
