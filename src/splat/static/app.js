"use strict";

const VERDICT = {
  worked: { label: "Worked", cls: "chip-success", color: "#3dd68c" },
  partial: { label: "Partly worked", cls: "chip-warn", color: "#c9a14a" },
  missed: { label: "Missed", cls: "chip-danger", color: "#ef6a6a" },
  ambiguous: { label: "Ambiguous", cls: "chip-info", color: "#8fb4ff" },
  too_early: { label: "Too early", cls: "chip-muted", color: "#4b4f57" },
  not_shipped: { label: "Not shipped", cls: "chip-muted", color: "#3a3d44" },
  no_success_metric: { label: "Never defined success", cls: "chip-muted", color: "#2a2c31" },
  insufficient_data: { label: "Not enough data", cls: "chip-muted", color: "#2a2c31" },
  unknown: { label: "Not in the graph", cls: "chip-muted", color: "#2a2c31" },
};
const ORDER = ["worked", "partial", "missed", "ambiguous", "too_early", "not_shipped", "no_success_metric"];

const SUGGESTIONS = [
  "Did caching invoice reads work?",
  "Did the guided onboarding checklist work?",
  "What breaks if A-1 is wrong?",
  "Which assumptions should we verify first?",
  "We are thinking of caching dashboard reads to cut latency. Has this been tried?",
  "Are two teams building the same thing?",
  "What work serves no goal?",
  "Did the blockchain rewrite work?",
];

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const chip = (v) => { const m = VERDICT[v] || VERDICT.unknown; return `<span class="chip ${m.cls}"><span class="dot"></span>${m.label}</span>`; };
const pct = (x) => (x > 0 ? "+" : "") + (x * 100).toFixed(1) + "%";
const loader = (text) => `<div class="loading fade-up"><span class="bars"><i></i><i></i><i></i></span>${esc(text)}</div><div class="progress"></div>`;

async function api(path) {
  const res = await fetch(path);
  if (res.status === 503) { showDown(true); throw new Error("database unavailable"); }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  showDown(false);
  return res.json();
}

function showDown(down) {
  $("#down").hidden = !down;
  const s = $("#status");
  s.className = "chip " + (down ? "chip-danger" : "chip-success chip-live");
  $("#status-text").textContent = down ? "Graph unreachable" : "Graph live";
}

// ---------------------------------------------------------------- navigation

function go(view) {
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.dataset.view !== view; });
  document.querySelectorAll(".dock button").forEach((b) => b.classList.toggle("active", b.dataset.go === view));
  window.scrollTo({ top: 0 });
  if (view === "graph") loadMap();
}
document.querySelectorAll(".dock button").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));

// ---------------------------------------------------------------- asking

$("#suggestions").innerHTML = SUGGESTIONS.map((q) => `<button type="button">${esc(q)}</button>`).join("");
$("#suggestions").addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") ask(e.target.textContent); });
$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q) ask(q); });

async function ask(q) {
  go("ask");
  $("#q").value = q;
  $("#answer").innerHTML = loader("Walking the graph");
  try {
    const a = await api("/api/ask?q=" + encodeURIComponent(q));
    $("#answer").innerHTML = renderAnswer(a);
  } catch (err) {
    $("#answer").innerHTML = `<div class="card answer fade-up"><h3>Could not answer</h3><p class="muted">${esc(err.message)}</p></div>`;
  }
}

function renderAnswer(a) {
  const d = a.data || {};
  let badge = "";
  if (a.tool === "verdict" && d.verdict) badge = chip(d.verdict);
  if (!a.data && /^Not in the graph/.test(a.title)) badge = chip("unknown");

  let facts = "";
  if (a.tool === "verdict" && d.expected) {
    const e = d.expected, m = d.measured;
    facts = `<div class="facts">
      <div class="fact"><div class="k">Promised</div><div class="v">${pct(e.change)}</div><div class="muted">${esc(e.metric_name)} by ${esc(e.judge_by)}</div></div>
      <div class="fact"><div class="k">Measured</div><div class="v">${m ? pct(m.change) : "—"}</div><div class="muted">${m ? `${m.baseline} → ${m.outcome} ${esc(e.unit)}` : "no result window yet"}</div></div>
      <div class="fact"><div class="k">Of target</div><div class="v">${d.achieved_share != null ? Math.round(d.achieved_share * 100) + "%" : "—"}</div><div class="muted">${d.overlaps && d.overlaps.length ? d.overlaps.length + " overlapping change(s)" : "no overlapping changes"}</div></div>
    </div>`;
  }

  const lines = (a.lines || []).map((l) => {
    const sub = /^\s{2,}/.test(l);
    return `<li class="${sub ? "sub" : ""}">${esc(l.trim())}</li>`;
  }).join("");

  let body = `<ul class="lines">${lines}</ul>`;
  if (a.tool === "blast_radius" && d.items) {
    body = `<ul class="lines"><li>${esc(a.lines[0])}</li></ul>
      <div class="section-k">What relies on ${esc(d.assumption)}</div>${renderTree(d)}
      <ul class="lines">${a.lines.filter((l) => /^(Goals|Metrics|People)/.test(l)).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`;
  }

  const path = a.path && a.path.length ? `<div class="section-k">Graph path</div><div class="path">${esc(a.path.join("\n"))}</div>` : "";
  const sources = a.sources && a.sources.length
    ? `<div class="section-k">Sources</div><div class="sources">${[...new Set(a.sources)].map((s) => `<button class="source-btn" data-source="${esc(s)}">${esc(s)}</button>`).join("")}</div>` : "";
  const trace = graphButton(a) + renderTrace(a.trace, a.ms, a.tool);

  // The badge already states the verdict, so drop the same words from the title.
  const title = badge ? a.title.replace(/^[^:]+:\s*/, "") : a.title;
  return `<div class="card answer fade-up">
    <div class="title">${badge}<h3>${esc(title)}</h3></div>
    ${facts}${body}${path}${sources}${trace}
  </div>`;
}

function renderTrace(queries, ms, tool) {
  if (!queries || !queries.length) return "";
  return `<details class="trace"><summary>How this was answered: ${queries.length} read-only graph quer${queries.length === 1 ? "y" : "ies"}${tool ? " via " + esc(tool) : ""}, ${ms} ms</summary>
    ${queries.map((q) => `<div class="q"><div class="qh">${esc(q.name)} · ${q.rows} row${q.rows === 1 ? "" : "s"} · ${q.ms} ms</div><div class="path">${esc(q.cypher)}</div></div>`).join("")}
  </details>`;
}

function renderTree(b) {
  const info = Object.fromEntries(b.items.map((i) => [i.id, i]));
  const kids = {};
  b.edges.forEach((e) => { (kids[e.parent] = kids[e.parent] || []).push(e.child); });
  const seen = new Set();
  const walk = (id) => {
    const children = (kids[id] || []).filter((c) => !seen.has(c));
    children.forEach((c) => seen.add(c));
    if (!children.length) return "";
    return `<ul>${children.map((c) => {
      const n = info[c] || { kind: "", title: c };
      const status = n.status ? ` <span class="chip chip-muted">${esc(n.status)}</span>` : (n.kind === "Task" ? ` <span class="chip chip-warn">open</span>` : "");
      return `<li><span class="node"><span class="kind">${esc(n.kind === "Project" ? "Epic" : n.kind)}</span><span class="id">${esc(c)}</span>${esc(n.title)}${status}</span>${walk(c)}</li>`;
    }).join("")}</ul>`;
  };
  return `<div class="tree"><span class="node"><span class="kind">Assumption</span><span class="id">${esc(b.assumption)}</span><span class="chip chip-danger">root</span></span>${walk(b.assumption)}</div>`;
}

// ---------------------------------------------------------------- graph drawing

const NODE = {
  Goal: { color: "#f5f5f5", shape: "round-rectangle", name: "Goal" },
  Initiative: { color: "#c9ccd2", shape: "round-rectangle", name: "Initiative" },
  Project: { color: "#8fb4ff", shape: "ellipse", name: "Epic" },
  Decision: { color: "#9aa0aa", shape: "ellipse", name: "Decision" },
  Assumption: { color: "#f5b544", shape: "diamond", name: "Assumption" },
  Metric: { color: "#b394f2", shape: "hexagon", name: "Metric" },
  Capability: { color: "#5ec4c4", shape: "round-triangle", name: "Capability" },
  Source: { color: "#6b7079", shape: "rectangle", name: "Source" },
  Incident: { color: "#ef6a6a", shape: "vee", name: "Incident" },
  Task: { color: "#4e535c", shape: "ellipse", name: "Task" },
  Person: { color: "#8a8f98", shape: "ellipse", name: "Person" },
  Team: { color: "#e0a3c8", shape: "round-octagon", name: "Team" },
};
const EDGE_TEXT = { RELIES_ON: "relies on" };
let verdictOf = {};
const ID_RE = /\b(?:D|A|EP|G|I|KES|GH|INC)-\d+\b|\b(?:docs|slack|incidents)\/[\w\-./]+/g;

function nodeColor(n) {
  if (n.label === "Decision" && verdictOf[n.id]) return VERDICT[verdictOf[n.id]].color;
  if (n.label === "Assumption") return n.disproved ? "#ef6a6a" : n.status === "supported" ? "#3dd68c" : "#f5b544";
  return (NODE[n.label] || NODE.Source).color;
}

function drawGraph(container, data, layout = "cose", onTap) {
  const elements = [
    ...data.nodes.map((n) => ({ data: { id: n.id, short: n.label === "Source" || n.label === "Person" || n.label === "Metric" ? n.title : n.id,
      title: n.title, kind: n.label, color: nodeColor(n), shape: (NODE[n.label] || NODE.Source).shape, raw: n } })),
    ...data.edges.map((e, i) => ({ data: { id: "e" + i, source: e.source, target: e.target, label: EDGE_TEXT[e.type] || e.type.toLowerCase().replace(/_/g, " ") } })),
  ];
  const cy = cytoscape({
    container, elements, wheelSensitivity: 0.25, minZoom: 0.2, maxZoom: 3,
    style: [
      { selector: "node", style: { "background-color": "data(color)", shape: "data(shape)", width: 30, height: 30,
        label: "data(short)", color: "#d9dce1", "font-size": 10, "font-family": "Roboto Mono, monospace", "min-zoomed-font-size": 5,
        "text-valign": "bottom", "text-margin-y": 5, "text-wrap": "ellipsis", "text-max-width": 120,
        "border-width": 1, "border-color": "#0b0b0d" } },
      { selector: "node[kind = 'Goal']", style: { width: 46, height: 34, "font-size": 12, color: "#fff" } },
      { selector: "edge", style: { width: 1.2, "line-color": "#3a3d44", "target-arrow-color": "#3a3d44",
        "target-arrow-shape": "triangle", "arrow-scale": 0.8, "curve-style": "bezier", label: "data(label)",
        "font-size": 7, color: "#6f747d", "text-rotation": "autorotate", "text-background-color": "#0b0b0d",
        "text-background-opacity": 1, "text-background-padding": 1, "min-zoomed-font-size": 9 } },
      { selector: "node:selected", style: { "border-width": 3, "border-color": "#fff" } },
      { selector: ".faded", style: { opacity: 0.15 } },
    ],
    layout: layout === "tree"
      ? { name: "breadthfirst", directed: true, spacingFactor: 1.1, padding: 20 }
      : { name: "cose", animate: false, nodeRepulsion: 6000, idealEdgeLength: 60, gravity: 1.2, padding: 16, componentSpacing: 40 },
  });
  cy.on("tap", "node", (evt) => {
    const node = evt.target;
    cy.elements().addClass("faded");
    node.closedNeighborhood().removeClass("faded");
    if (onTap) onTap(node.data("raw"));
  });
  cy.on("tap", (evt) => { if (evt.target === cy) cy.elements().removeClass("faded"); });
  cy.ready(() => cy.fit(undefined, 16));
  container.cy = cy;
  return cy;
}

function legendHtml(labels) {
  return labels.map((l) => `<span class="swatch"><i style="background:${NODE[l].color}"></i>${NODE[l].name}</span>`).join("")
    + `<span class="swatch"><i style="background:#ef6a6a"></i>Disproved</span><span class="swatch"><i style="background:#3dd68c"></i>Worked / supported</span>`;
}

// Draw the subgraph behind an answer, on demand.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-graph]");
  if (!btn) return;
  const box = btn.nextElementSibling;
  if (!box.hidden) { box.hidden = true; btn.textContent = "Show the graph behind this answer"; return; }
  box.hidden = false;
  btn.textContent = "Hide graph";
  box.innerHTML = "";
  const payload = JSON.parse(btn.dataset.graph);
  if (payload.blast) {
    drawGraph(box, payload.blast, "tree");
    return;
  }
  try {
    const g = await api("/api/graph?ids=" + encodeURIComponent(payload.ids.join(",")));
    drawGraph(box, g, "cose");
  } catch (err) {
    box.innerHTML = `<p class="muted" style="padding:12px">${esc(err.message)}</p>`;
  }
});

function graphButton(a) {
  let payload;
  if (a.tool === "blast_radius" && a.data && a.data.items) {
    const b = a.data;
    payload = { blast: {
      nodes: [{ id: b.assumption, label: "Assumption", title: b.assumption, disproved: false },
        ...b.items.map((i) => ({ id: i.id, label: i.kind, title: i.title, status: i.status }))],
      edges: b.edges.map((e) => ({ source: e.child, target: e.parent, type: "RELIES_ON" })) } };
  } else {
    const ids = [...new Set((JSON.stringify(a.data || {}) + " " + (a.sources || []).join(" ")).match(ID_RE) || [])].slice(0, 60);
    if (!ids.length) return "";
    payload = { ids };
  }
  return `<button class="btn toggle-graph" data-graph='${esc(JSON.stringify(payload))}'>Show the graph behind this answer</button><div class="graph-canvas" hidden></div>`;
}

// The company map view.
let mapDrawn = false;
async function loadMap() {
  if (mapDrawn) return;
  mapDrawn = true;
  $("#map-legend").innerHTML = legendHtml(["Goal", "Initiative", "Project", "Decision", "Assumption", "Metric", "Capability"]);
  $("#map").innerHTML = loader("Drawing the company graph");
  try {
    const m = await api("/api/map");
    $("#map").innerHTML = "";
    drawGraph($("#map"), m, "cose", showNode);
    $("#map-trace").innerHTML = renderTrace(m.queries, m.ms, "company_map");
  } catch (err) {
    mapDrawn = false;
    $("#map").innerHTML = `<p class="muted" style="padding:16px">${esc(err.message)}</p>`;
  }
}

function showNode(n) {
  const kind = (NODE[n.label] || {}).name || n.label;
  const question = {
    Decision: `Did ${n.id} work?`, Assumption: `What breaks if ${n.id} is wrong?`,
    Project: `Why does ${n.id} exist?`, Initiative: `Why does ${n.id} exist?`,
  }[n.label];
  const verdict = n.label === "Decision" && verdictOf[n.id] ? chip(verdictOf[n.id]) : "";
  const status = n.label === "Assumption" ? chip(n.disproved ? "missed" : "too_early").replace(/Missed|Too early/, n.disproved ? "Disproved" : n.status || "Untested") : "";
  $("#map-info").innerHTML = `<span class="chip chip-muted">${esc(kind)}</span> ${verdict}${status}
    <h4>${esc(n.title)}</h4><div class="mono muted">${esc(n.id)}</div>
    ${question ? `<button class="btn btn-primary" data-ask="${esc(question)}">${esc(question)}</button>` : ""}`;
}

// ---------------------------------------------------------------- sources

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-source]");
  if (!btn) return;
  const id = btn.dataset.source;
  $("#drawer").hidden = false;
  $("#drawer-body").innerHTML = loader("Opening " + id);
  try {
    const s = await api("/api/source/" + id.split("/").map(encodeURIComponent).join("/"));
    const text = s.kind === "slack"
      ? s.text.split("\n").map((l) => { const i = l.indexOf(": "); return `<div class="msg"><b>${esc(l.slice(0, i))}</b>${esc(l.slice(i + 2))}</div>`; }).join("")
      : `<div class="doc">${esc(s.text)}</div>`;
    $("#drawer-body").innerHTML = `<span class="chip chip-muted">${esc(s.kind === "slack" ? "Slack thread" : "Document")}</span>
      <h3>${esc(s.title)}</h3><div class="muted">${esc(s.author || "")} · ${esc(s.at)}</div>
      <div class="muted mono" style="margin-top:6px">${esc(s.id)}</div>${s.kind === "slack" ? `<div style="margin-top:14px">${text}</div>` : text}`;
  } catch (err) {
    $("#drawer-body").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
});
$("#drawer-close").addEventListener("click", () => { $("#drawer").hidden = true; });
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") $("#drawer").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });

// ---------------------------------------------------------------- overview

async function loadOverview() {
  try {
    const health = await api("/api/health");
    if (!health.loaded) { $("#tiles").innerHTML = `<div class="card">The graph is empty. Run <code>uv run splat load</code>.</div>`; return; }
    const o = await api("/api/overview");
    $("#company").textContent = `Kestrel · data as of ${o.as_of}`;
    verdictOf = Object.fromEntries(o.scorecard.decisions.map((d) => [d.id, d.verdict]));
    renderTiles(o);
    renderOutcomes(o.scorecard);
    renderAssumptions(o);
    renderAlignment(o);
  } catch (err) {
    $("#tiles").innerHTML = `<div class="card muted">${esc(err.message)}</div>`;
  }
}

function renderTiles(o) {
  const c = o.scorecard.counts;
  const tile = (k, v, s) => `<div class="card tile fade-up"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  const orphans = o.orphaned.projects.length + o.orphaned.tasks.length;
  $("#tiles").innerHTML =
    tile("Decisions judged", o.scorecard.total, `${c.worked || 0} worked · ${c.missed || 0} missed`) +
    tile("Never defined success", c.no_success_metric || 0, "no measurable promise to check") +
    tile("Assumptions disproved", o.cascade.length, `${o.cascade.reduce((n, a) => n + a.decisions.length, 0)} decisions rested on them`) +
    tile("Work with no live goal", orphans, `${o.duplicates.length} duplicate effort${o.duplicates.length === 1 ? "" : "s"} found`);
}

function renderOutcomes(s) {
  const total = s.total;
  $("#stack").innerHTML = ORDER.filter((k) => s.counts[k]).map((k) =>
    `<span title="${VERDICT[k].label}: ${s.counts[k]}" style="width:${(s.counts[k] / total) * 100}%;background:${VERDICT[k].color}"></span>`).join("");
  $("#legend").innerHTML = ORDER.filter((k) => s.counts[k]).map((k) => `${chip(k)}<span class="muted" style="margin-right:6px">${s.counts[k]}</span>`).join("");
  const order = (v) => ORDER.indexOf(v);
  $("#decisions").innerHTML = [...s.decisions].sort((a, b) => order(a.verdict) - order(b.verdict) || a.id.localeCompare(b.id)).map((d) =>
    `<button class="row" data-ask="Did ${esc(d.id)} work?"><span class="id">${esc(d.id)}</span>
      <span class="t"><div>${esc(d.title)}</div><div class="r">${esc(d.reason)}</div></span>${chip(d.verdict)}<span class="go">→</span></button>`).join("");
}

function renderAssumptions(o) {
  $("#disproved").innerHTML = o.cascade.map((a) => `<div class="card assumption fade-up">
      <div class="top-line"><div><span class="mono muted">${esc(a.id)}</span> <span class="stmt">${esc(a.statement)}</span></div>
        <button class="btn" data-blast="${esc(a.id)}">Blast radius</button></div>
      <div class="ev">Disproved on ${esc(a.contradicted_at)} by <button class="source-btn" data-source="${esc(a.evidence.id)}">${esc(a.evidence.id)}</button> · ${esc(a.evidence.author)}</div>
      <div class="decs">${a.decisions.map((d) => `<div class="dec">${chip(d.verdict)}<span class="mono muted">${esc(d.id)}</span>${esc(d.title)}
        ${d.decided_after_evidence ? `<span class="chip chip-danger">decided after the evidence</span>` : ""}
        ${d.still_live ? `<span class="chip chip-warn">still live</span>` : ""}
        ${(d.verdict === "worked" || d.verdict === "partial") ? `<span class="chip chip-info">not for the stated reason</span>` : ""}</div>`).join("")}</div>
    </div>`).join("");
  const untested = o.load_bearing.filter((a) => a.status === "untested");
  $("#verify").innerHTML = untested.map((a, i) => `<button class="row" data-blast="${esc(a.id)}"><span class="id">#${i + 1}</span>
      <span class="t"><div>${esc(a.statement)}</div><div class="r">${esc(a.id)} · ${a.dependents} decisions, epics and open tasks rest on it</div></span>
      <span class="num">${a.score.toFixed(4)}</span><span class="go">blast radius →</span></button>`).join("");
}

function renderAlignment(o) {
  const card = (title, sub, count, items) => `<div class="card fade-up"><span class="count">${count}</span><h3>${title}</h3>
    <div class="muted" style="font-size:13px">${sub}</div><ul>${items.join("") || `<li class="muted">Nothing found.</li>`}</ul></div>`;
  const orphanItems = [
    ...o.orphaned.projects.map((p) => `<li><span class="id">${esc(p.id)}</span>${esc(p.title)} <span class="muted">· ${esc(p.team)} · ${p.reaches.length ? "only reaches " + esc(p.reaches.join(", ")) : "no path to any goal"}</span></li>`),
    ...o.orphaned.tasks.map((t) => `<li><span class="id">${esc(t.id)}</span>${esc(t.title)} <span class="muted">· not part of any epic</span></li>`),
  ];
  const dup = o.duplicates.map((x) => `<li>${esc(x.projects[0].team)} <span class="id">${esc(x.projects[0].id)}</span>${esc(x.projects[0].title)}
      <br>${esc(x.projects[1].team)} <span class="id">${esc(x.projects[1].id)}</span>${esc(x.projects[1].title)}
      <br><span class="muted">Both build “${esc(x.capability)}”. ${x.linked ? "Linked." : "No shared person or thread."}
      ${x.same_island ? "" : "Separate conversation islands: " + x.islands.map((i) => "{" + i.teams.join(", ") + "}").join(" and ") + "."}</span></li>`);
  $("#alignment").innerHTML =
    card("Work that serves no live goal", "Active epics and open tickets with no path to an active goal.", orphanItems.length, orphanItems) +
    card("Goals nobody is working on", "Active goals with no active epic under them.", o.goals_without_work.length,
      o.goals_without_work.map((g) => `<li><span class="id">${esc(g.id)}</span>${esc(g.title)} <span class="muted">· owner ${esc(g.owner)}</span></li>`)) +
    card("Work under cancelled goals", "Epics still moving after their goal was cancelled.", o.cancelled_goal_work.length,
      o.cancelled_goal_work.map((x) => `<li><span class="id">${esc(x.project.id)}</span>${esc(x.project.title)} <span class="muted">· ${esc(x.goal.title)} cancelled ${esc(x.goal.cancelled_at)} · ${x.tasks_after_cancel} tickets since</span></li>`)) +
    card("Metrics nobody owns", "No owner, or the owner has left the company.", o.unowned_metrics.length,
      o.unowned_metrics.map((m) => `<li>${esc(m.name)} <span class="muted">· ${m.former_owner ? esc(m.former_owner) + " left on " + esc(m.owner_left_on) : "never had an owner"}</span></li>`)) +
    card("Teams building the same thing", "Same capability, different teams, no sign they know.", o.duplicates.length, dup);
}

// ---------------------------------------------------------------- blast radius from the assumptions view

document.addEventListener("click", async (e) => {
  const blastBtn = e.target.closest("[data-blast]");
  if (blastBtn) {
    const id = blastBtn.dataset.blast;
    $("#blast").innerHTML = loader("Following everything that relies on " + id);
    $("#blast").scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const b = await api("/api/blast/" + encodeURIComponent(id));
      const people = b.people.length ? `<div class="section-k">People to tell</div><div>${b.people.map(esc).join(", ")}</div>` : "";
      const goals = b.goals.length ? `<div class="section-k">Goals at risk</div><div>${b.goals.map(esc).join("; ")}</div>` : "";
      $("#blast").innerHTML = `<div class="card answer fade-up"><div class="title"><h3>What relies on ${esc(id)}</h3>
          <span class="chip chip-muted">${b.decisions.length} decisions · ${b.epics.length} epics · ${b.open_tasks.length} open tasks · ${b.max_hops} steps deep</span></div>
        <div style="margin-top:16px">${b.items.length ? renderTree(b) : `<p class="muted">Nothing in the graph relies on it.</p>`}</div>
        ${goals}${people}${renderTrace(b.queries, b.ms, "blast_radius")}</div>`;
    } catch (err) {
      $("#blast").innerHTML = `<div class="card muted">${esc(err.message)}</div>`;
    }
    return;
  }
  const askBtn = e.target.closest("[data-ask]");
  if (askBtn) ask(askBtn.dataset.ask);
});

loadOverview();
