// Indian Affairs LOD Demo — page interactivity
// Loads pre-rendered case study and TF-IDF data and renders timelines, maps, charts.

async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`Failed to load ${path}: ${r.status}`);
  return r.json();
}

function renderTimeline(container, entries) {
  const ol = document.createElement('ol');
  for (const e of entries) {
    const li = document.createElement('li');
    li.innerHTML = `<span class="year">${e.year}</span> — ${e.summary}` +
      (e.role ? `<span class="role">${e.role}${e.place ? ' · ' + e.place : ''}</span>` : '');
    ol.appendChild(li);
  }
  container.innerHTML = '';
  container.appendChild(ol);
}

function renderSnippets(container, snippets) {
  if (!snippets || !snippets.length) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = '<h4>Passages from the reports</h4>';
  for (const s of snippets) {
    const div = document.createElement('div');
    div.className = 'snippet';
    div.innerHTML = `"${s.text}"<span class="source">— ${s.source}</span>`;
    container.appendChild(div);
  }
}

function renderMap(mapId, places) {
  if (!places || !places.length) return;
  const map = L.map(mapId, { scrollWheelZoom: false });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap',
    maxZoom: 10
  }).addTo(map);
  const markers = [];
  for (const p of places) {
    if (p.lat == null || p.lon == null) continue;
    const m = L.marker([p.lat, p.lon]).addTo(map);
    m.bindPopup(`<strong>${p.name}</strong><br>${p.years || ''}`);
    markers.push(m);
  }
  if (markers.length) {
    const group = L.featureGroup(markers);
    map.fitBounds(group.getBounds().pad(0.3));
  } else {
    map.setView([54, -105], 3);
  }
}

function renderTFIDF(container, data) {
  container.innerHTML = '';
  const regions = [
    { key: 'bc', label: 'British Columbia' },
    { key: 'prairies', label: 'Prairies / North-West Territories' },
    { key: 'quebec', label: 'Quebec' }
  ];
  for (const reg of regions) {
    const r = data[reg.key];
    if (!r) continue;
    const div = document.createElement('div');
    div.className = `tfidf-region ${reg.key}`;
    let html = `<h4>${reg.label}</h4>`;
    const max = Math.max(...r.top_terms.map(t => t.score));
    for (const t of r.top_terms) {
      const pct = (t.score / max * 100).toFixed(1);
      html += `<div class="tfidf-bar"><span class="term">${t.term}</span>` +
              `<span class="bar"><span class="fill" style="width:${pct}%"></span></span></div>`;
    }
    html += `<p class="meta">${r.n_sections} sections · ${r.n_agents} agents</p>`;
    div.innerHTML = html;
    container.appendChild(div);
  }
}

// Queries pre-loaded as YASGUI tabs. First line of each file is the summary.
const YASGUI_TABS = [
  { file: 'queries/01_dewdney_career.rq',      name: 'Dewdney career' },
  { file: 'queries/02_agents_at_place.rq',     name: 'Agents at Battleford' },
  { file: 'queries/03_boucher_two_uris.rq',    name: 'Boucher disambiguation' },
  { file: 'queries/04_agents_by_region_1885.rq', name: 'Agents by place (1885)' },
];

async function initYasgui() {
  if (typeof Yasgui === 'undefined') {
    console.warn('Yasgui not loaded');
    return;
  }
  const mount = document.getElementById('yasgui');
  if (!mount) return;
  const yasgui = new Yasgui(mount, {
    requestConfig: { endpoint: 'https://fuseki.lincsproject.ca/lincs/sparql' },
    copyEndpointOnNewTab: true,
  });
  // Remove the default empty tab, add ours.
  const existing = Object.keys(yasgui._tabs || {});
  for (let i = 0; i < YASGUI_TABS.length; i++) {
    const t = YASGUI_TABS[i];
    let body = '';
    try {
      const r = await fetch(t.file);
      body = (await r.text()).replace(/^#\+.*\n/gm, '').trim();
    } catch (e) {
      body = `# Failed to load ${t.file}`;
    }
    const tab = yasgui.addTab(
      i === 0,  // set active on first
      {
        ...Yasgui.Tab.getDefaults(),
        name: t.name,
        requestConfig: { endpoint: 'https://fuseki.lincsproject.ca/lincs/sparql' },
        yasqe: { value: body },
      }
    );
  }
  // Close the auto-created empty tab(s) that existed before ours
  for (const id of existing) {
    try { yasgui.getTab(id).close(); } catch (_) {}
  }
}

async function init() {
  // Case studies
  try {
    const data = await loadJSON('data/agent-timelines.json');
    for (const agent of ['dewdney', 'powell', 'boucher']) {
      const a = data[agent];
      if (!a) continue;
      const blurb = document.querySelector(`.case-blurb[data-agent="${agent}"]`);
      if (blurb && a.blurb) blurb.textContent = a.blurb;
      const tl = document.querySelector(`.case-timeline[data-agent="${agent}"]`);
      if (tl) renderTimeline(tl, a.timeline || []);
      const sn = document.querySelector(`.case-snippets[data-agent="${agent}"]`);
      if (sn) renderSnippets(sn, a.snippets || []);
      renderMap(`map-${agent}`, a.places || []);
    }
  } catch (e) {
    console.error('Case studies:', e);
  }

  // YASGUI — live SPARQL against LINCS Fuseki
  try {
    await initYasgui();
  } catch (e) {
    console.error('YASGUI:', e);
  }

  // Regional TF-IDF
  try {
    const tfidf = await loadJSON('data/regional_tfidf.json');
    const container = document.getElementById('tfidf-charts');
    renderTFIDF(container, tfidf);
    const methodEl = document.getElementById('tfidf-method');
    if (methodEl && tfidf.method) {
      const m = tfidf.method;
      methodEl.innerHTML =
        `<strong>Method:</strong> of ${m.total_sections_considered} sections in 1880–1885, ` +
        `${m.dropped_no_grounded_agents} were dropped (no agents matched to LINCS), ` +
        `${m.dropped_outside_three_regions} were dropped (agents serve outside BC / Prairies / Quebec — ` +
        `mostly Ontario and the Maritimes), and ${m.dropped_no_majority_region} were dropped ` +
        `(ambiguous region vote). TF-IDF is computed across three region-level “documents” with a ` +
        `minimum term frequency of ${m.min_term_frequency}. Terms appearing in all three regions ` +
        `are excluded (they carry no distinguishing power).`;
    }
  } catch (e) {
    console.error('TF-IDF:', e);
  }
}

document.addEventListener('DOMContentLoaded', init);
