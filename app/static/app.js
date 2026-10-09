// Roadie front end: vanilla JS + Leaflet. All API data is inserted with
// textContent (never innerHTML) so venue/artist names cannot inject markup.
"use strict";

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const fmt = (n, d = 0) =>
  n === null || n === undefined ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
const money = (n) => (n === null || n === undefined ? "—" : "$" + fmt(n));

// ---- map -------------------------------------------------------------------
const map = L.map("map", { scrollWheelZoom: false }).setView([39, -98], 4);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
let layers = L.layerGroup().addTo(map);

// Leaflet renders string tooltips as HTML, so always pass a text-only node.
const tip = (text) => el("span", { text });

function heatColor(v) {
  const t = Math.max(0, Math.min(1, v || 0));
  const r = 242, g = Math.round(200 - 110 * t), b = Math.round(90 - 60 * t);
  return `rgb(${r},${g},${b})`;
}

function drawMap(data) {
  layers.clearLayers();
  const bounds = [];
  const region = data.region;
  const home = [region.lat, region.lon];
  bounds.push(home);
  L.circleMarker(home, { radius: 6, color: "#1d1b19", fillColor: "#fff", fillOpacity: 1, weight: 2 })
    .bindTooltip(tip(`Home: ${region.name}`)).addTo(layers);

  const route = data.grounded.route;
  const stopNames = new Set((route?.stops || []).map((s) => s.city));
  for (const c of data.grounded.candidates || []) {
    if (stopNames.has(c.city)) continue;
    L.circleMarker([c.lat, c.lon], { radius: 4, color: "#b9b2a7", fillColor: "#b9b2a7", fillOpacity: 0.7, weight: 1 })
      .bindTooltip(tip(`${c.city}: Qloo score ${fmt(c.score, 2)} (not routed)`)).addTo(layers);
  }
  if (route) {
    for (const s of route.stops) {
      for (const h of s.hotspots || []) {
        L.circle([h.lat, h.lon], { radius: 900 + 2600 * (h.affinity || 0), stroke: false, fillColor: heatColor(h.affinity), fillOpacity: 0.35 })
          .addTo(layers);
      }
    }
    const pts = [home, ...route.stops.map((s) => [s.lat, s.lon]), home];
    L.polyline(pts, { color: "#d2491f", weight: 4, opacity: 0.9 }).addTo(layers);
    route.stops.forEach((s, i) => {
      bounds.push([s.lat, s.lon]);
      const icon = L.divIcon({ className: "", html: `<div class="num-marker">${i + 1}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });
      const venue = s.venues?.[0]?.name ? ` · ${s.venues[0].name}` : "";
      L.marker([s.lat, s.lon], { icon }).bindTooltip(tip(`${i + 1}. ${s.city}${venue}`)).addTo(layers);
    });
  }
  const base = (data.baseline?.stops || []).filter((s) => typeof s.lat === "number" && typeof s.lon === "number");
  if (base.length) {
    const pts = [home, ...base.map((s) => [s.lat, s.lon]), home];
    L.polyline(pts, { color: "#3a5a8c", weight: 3, opacity: 0.8, dashArray: "6 8" }).addTo(layers);
    base.forEach((s) => {
      bounds.push([s.lat, s.lon]);
      L.circleMarker([s.lat, s.lon], { radius: 5, color: "#3a5a8c", fillColor: "#fff", fillOpacity: 1, weight: 2 })
        .bindTooltip(tip(`LLM-only: ${s.city}`)).addTo(layers);
    });
  }
  if (bounds.length > 1) map.fitBounds(bounds, { padding: [30, 30] });
}

// ---- rendering ---------------------------------------------------------------
function renderModes(modes) {
  const box = $("#modes");
  box.replaceChildren(
    el("span", { class: `badge ${modes.qloo === "mock" ? "mock" : "live"}`, text: modes.qloo === "mock" ? "Qloo: mock fixtures (synthetic)" : "Qloo: live hackathon API" }),
    el("span", { class: `badge ${modes.llm === "scripted-mock" ? "mock" : "live"}`, text: modes.llm === "scripted-mock" ? "LLM: scripted mock" : `LLM: ${modes.llm}` }),
    el("span", { class: "badge", text: modes.tracing ? `LangSmith: ${modes.langsmith_project}` : "Tracing off" }),
  );
}

function stat(label, value, cls = "") {
  return el("div", { class: `stat ${cls}` }, el("b", { text: value }), label);
}

function renderItinerary(data) {
  const g = data.grounded;
  const r = g.route;
  $("#summary").textContent = g.summary || (r ? "" : "No route could be built from the Qloo results.");
  const traceLink = $("#trace");
  if (data.trace_url) { traceLink.href = data.trace_url; traceLink.hidden = false; } else { traceLink.hidden = true; }
  if (!r) { $("#stops").replaceChildren(); $("#money").replaceChildren(); return; }
  $("#money").replaceChildren(
    stat("shows", String(r.stops.length)),
    stat("days", `${r.days.length} / ${data.request?.tour_days ?? r.days.length}`),
    stat("km driven", fmt(r.total_km)),
    stat(`est. cost vs ${money(r.budget)}`, money(r.cost.total), r.within_budget ? "ok" : "over"),
    stat("break-even per show", money(r.break_even_per_show)),
  );
  $("#stops").replaceChildren(...r.stops.map((s) => {
    const venues = (s.venues || []).map((v) => `${v.name}${v.affinity != null ? ` (${fmt(v.affinity, 2)})` : ""}`).join(", ");
    const openers = (s.openers || []).map((o) => `${o.name} (fit ${fmt(o.fit, 2)})`).join(", ");
    return el("li", {},
      el("span", { class: "city", text: s.city }), " ", el("span", { class: "score", text: `Qloo score ${fmt(s.score, 2)}` }),
      s.why ? el("p", { class: "why", text: s.why }) : null,
      el("p", { class: "sub" }, el("b", { text: "Rooms: " }), venues || "No venue match in Qloo for this city"),
      el("p", { class: "sub" }, el("b", { text: "Openers: " }), openers || "No act with a strong local Qloo audience; add a shortlist to score your own"),
    );
  }));
  $("#days").replaceChildren(
    el("tr", {}, el("th", { text: "Day" }), el("th", { text: "Date" }), el("th", { text: "What" }), el("th", { text: "Where" }), el("th", { text: "Drive" })),
    ...r.days.map((d) => el("tr", {}, el("td", { text: d.day }), el("td", { text: d.date }), el("td", { text: d.kind }), el("td", { text: d.city }), el("td", { text: `${fmt(d.drive_km)} km` }))),
  );
  const risks = [...(g.risks || []), ...(r.notes || [])];
  $("#risks").replaceChildren(...[...new Set(risks)].map((x) => el("li", { text: x })));
}

function metricRows(m, route) {
  const rows = [
    ["Stops", m?.stops],
    ["Cities in Qloo's ranked list", m?.cities_in_qloo_top],
    ["Mean audience affinity", m?.mean_affinity != null ? fmt(m.mean_affinity, 3) : "—"],
    ["Venues found in Qloo", m?.venues_verified],
    ["Openers found in Qloo", m?.openers_verified],
  ];
  if (route) {
    rows.push(["Km driven", fmt(route.total_km)]);
    rows.push(["Est. cost", money(route.cost?.total)]);
    rows.push(["Fits budget", route.within_budget ? "yes" : "no"]);
    if (route.fits_days !== undefined) rows.push(["Fits tour length", route.fits_days ? "yes" : "no"]);
  }
  return rows.map(([k, v]) => el("div", { class: "metric" }, el("span", { text: k }), el("b", { text: v ?? "—" })));
}

function renderCompare(data) {
  const box = $("#compare");
  const audit = data.baseline?.audit;
  if (!audit) {
    box.replaceChildren(el("p", { class: "muted", text: data.baseline?.error ? `LLM-only plan failed: ${data.baseline.error}` : "Comparison skipped." }));
    return;
  }
  const g = data.grounded;
  const left = el("div", { class: "side baseline" }, el("h3", { text: "LLM-only (no tools)" }),
    ...metricRows(audit.metrics, audit.route),
    el("ol", {}, ...audit.stops.map((s) => el("li", {},
      `${s.city}`, s.qloo_rank ? ` · Qloo rank #${s.qloo_rank}` : " · not in Qloo's list",
      s.venue ? el("span", { class: `flag ${s.venue_in_qloo ? "yes" : "no"}`, text: s.venue_in_qloo ? "venue found" : "venue not found" }) : null,
    ))),
  );
  const gr = g.route ? { ...g.route, fits_days: g.route.days.length <= (data.request?.tour_days ?? 999) } : null;
  const right = el("div", { class: "side grounded" }, el("h3", { text: "Roadie (Qloo-grounded)" }),
    ...metricRows(g.metrics, gr),
    el("ol", {}, ...(g.route?.stops || []).map((s) => el("li", { text: `${s.city} · score ${fmt(s.score, 2)}` }))),
  );
  box.replaceChildren(left, right);
}

function renderSteps(data) {
  const steps = data.grounded.steps || [];
  $("#steps").replaceChildren(...steps.map((s) => {
    const calls = s.qloo_calls || [];
    return el("li", {},
      el("b", { text: s.tool }), s.auto ? " (auto-completed)" : "", ` · ${calls.length} Qloo call${calls.length === 1 ? "" : "s"}`,
      s.error ? el("span", { class: "flag no", text: s.error }) : null,
      calls.length ? el("details", {}, el("summary", { text: "requests" }),
        ...calls.map((c) => el("div", {}, el("code", { text: `GET ${c.path}?${new URLSearchParams(c.params).toString()}` }),
          ` → ${c.status}, ${c.result_count} results${c.cached ? " (cached)" : ""}`))) : null,
    );
  }));
  $("#warnings").replaceChildren(...(data.warnings || []).map((w) => el("li", { text: w })));
}

// ---- form ------------------------------------------------------------------
const form = $("#plan-form");
const splitList = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);

function fillForm(p) {
  form.artists.value = p.artists.join(", ");
  form.home_region.value = p.home_region;
  form.tour_days.value = p.tour_days;
  form.budget_usd.value = p.budget_usd;
  form.band_size.value = p.band_size ?? 3;
}

async function boot() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    renderModes(cfg.modes);
    $("#regions").replaceChildren(...cfg.regions.map((r) => el("option", { value: r })));
    $("#presets").replaceChildren(...cfg.presets.map((p) => {
      const b = el("button", { type: "button", class: "chip", text: p.label });
      b.addEventListener("click", () => { fillForm(p); form.requestSubmit(); });
      return b;
    }));
    if (cfg.presets[0]) fillForm(cfg.presets[0]);
  } catch (e) {
    $("#status").textContent = "Could not load config.";
  }
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const status = $("#status");
  const body = {
    artists: splitList(form.artists.value),
    home_region: form.home_region.value.trim(),
    tour_days: Number(form.tour_days.value),
    budget_usd: Number(form.budget_usd.value),
    band_size: Number(form.band_size.value),
    local_openers: splitList(form.local_openers.value),
    include_baseline: form.include_baseline.checked,
  };
  if (form.start_date.value) body.start_date = form.start_date.value;
  if (!body.artists.length || !body.home_region) {
    status.textContent = "Add at least one artist and a home region.";
    status.className = "status err";
    return;
  }
  $("#go").disabled = true;
  status.className = "status";
  status.textContent = "Resolving artists, ranking cities by audience affinity, finding rooms and openers…";
  try {
    const res = await fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    data.request = body;
    renderModes(data.modes);
    $("#results").hidden = false;
    renderItinerary(data);
    renderCompare(data);
    renderSteps(data);
    drawMap(data);
    status.textContent = `Done in ${fmt(data.elapsed_ms / 1000, 1)}s${data.cached ? " (cached)" : ""} · ${data.qloo_calls.length} Qloo calls.`;
  } catch (e) {
    status.textContent = `Planning failed: ${e.message}`;
    status.className = "status err";
  } finally {
    $("#go").disabled = false;
  }
});

boot();
