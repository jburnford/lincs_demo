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

function renderNeighborhood(container, neighborhood) {
  if (!container || !neighborhood) return;
  const { reports_in = [], administers = [], bands = [], schools = [] } = neighborhood;
  const topBands = bands.slice(0, 8);
  const topSchools = schools.slice(0, 6);
  const topReports = reports_in.slice(0, 8);
  const topAdmin = administers.slice(0, 6);

  if (!topBands.length && !topSchools.length && !topReports.length && !topAdmin.length) {
    container.innerHTML = '';
    return;
  }

  const chip = e => {
    const yr = e.year_range ? ` <span class="yr">${e.year_range}</span>` : '';
    const ct = e.count > 1 ? ` <span class="ct">×${e.count}</span>` : '';
    return `<li>${e.name}${yr}${ct}</li>`;
  };

  let html = '<h4>Knowledge-graph neighborhood</h4><div class="neighborhood-grid">';
  if (topReports.length) {
    html += '<div class="nb-col"><h5>Who reports to them</h5><ul>' +
            topReports.map(chip).join('') + '</ul></div>';
  }
  if (topAdmin.length) {
    html += '<div class="nb-col"><h5>What they administer</h5><ul>' +
            topAdmin.map(chip).join('') + '</ul></div>';
  }
  if (topBands.length) {
    html += '<div class="nb-col"><h5>Bands and communities named</h5><ul>' +
            topBands.map(chip).join('') + '</ul></div>';
  }
  if (topSchools.length) {
    html += '<div class="nb-col"><h5>Schools named</h5><ul>' +
            topSchools.map(chip).join('') + '</ul></div>';
  }
  html += '</div>';
  html += '<p class="nb-caveat">Extracted from NER relationships and co-occurring entities in sections where the agent is an active participant. Unreviewed — some predicate-level noise remains (see audit notes under the network panel).</p>';
  container.innerHTML = html;
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

function renderMap(mapId, places, mapNote) {
  const container = document.getElementById(mapId);
  if (!container) return;
  if (!places || !places.length) {
    // No settlement-level places — show a note instead of empty map
    if (mapNote) {
      container.innerHTML = `<p class="placeholder" style="padding:1rem">${mapNote}</p>`;
    }
    return;
  }
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
  // Show territory-level caveat below the map
  if (mapNote) {
    const note = document.createElement('p');
    note.className = 'map-note';
    note.textContent = mapNote;
    container.parentNode.insertBefore(note, container.nextSibling);
  }
}

const REGION_COLORS = {
  bc:       '#2c6e85',
  prairies: '#b87333',
  quebec:   '#4a6e3a',
  other:    '#7a7263',
};

function renderPlaceMap(payload) {
  const mapEl = document.getElementById('place-map-full');
  if (!mapEl || !payload || !payload.places || !payload.places.length) return;

  const map = L.map('place-map-full', { scrollWheelZoom: false })
               .setView([54, -96], 4);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap',
    maxZoom: 10,
  }).addTo(map);

  // Radius scales with mention count (log-ish). Min 4, max 18.
  const counts = payload.places.map(p => p.total_mentions);
  const maxCount = Math.max(...counts, 1);
  const radiusFor = n =>
    Math.max(4, Math.min(18, 4 + 14 * Math.log10(1 + n) / Math.log10(1 + maxCount)));

  const markers = [];
  for (const p of payload.places) {
    const color = REGION_COLORS[p.region] || REGION_COLORS.other;
    const circle = L.circleMarker([p.lat, p.lon], {
      radius: radiusFor(p.total_mentions),
      color: color,
      weight: 1,
      fillColor: color,
      fillOpacity: 0.55,
    });
    const yearList = Object.keys(p.by_year).sort();
    const first = yearList[0];
    const last = yearList[yearList.length - 1];
    circle.bindPopup(
      `<strong>${p.label}</strong><br>` +
      `${p.total_mentions} mention${p.total_mentions === 1 ? '' : 's'} ` +
      `in ${p.section_count} section${p.section_count === 1 ? '' : 's'}<br>` +
      `<small>${first}${first !== last ? '–' + last : ''} · ${p.region}</small><br>` +
      `<small><a href="${p.uri}" target="_blank">${p.uri}</a></small>`
    );
    circle.__place = p;
    circle.addTo(map);
    markers.push(circle);
  }

  // Year slider: filters by cumulative mentions up to the selected year.
  const slider = document.getElementById('place-year-slider');
  const label  = document.getElementById('place-year-label');
  const stats  = document.getElementById('place-map-stats');

  function update(year) {
    let visible = 0;
    let totalMentions = 0;
    for (const m of markers) {
      const p = m.__place;
      let cum = 0;
      for (const [y, c] of Object.entries(p.by_year)) {
        if (+y <= year) cum += c;
      }
      if (cum > 0) {
        if (!map.hasLayer(m)) m.addTo(map);
        m.setRadius(radiusFor(cum));
        visible++;
        totalMentions += cum;
      } else if (map.hasLayer(m)) {
        map.removeLayer(m);
      }
    }
    label.textContent = `cumulative through ${year}`;
    if (stats) {
      stats.innerHTML =
        `<strong>${visible}</strong> distinct places · ` +
        `<strong>${totalMentions}</strong> grounded mentions · ` +
        `by region: ` +
        Object.entries(payload.stats.by_region)
          .map(([k, v]) => `${v} ${payload.regions[k].split(' /')[0].toLowerCase()}`)
          .join(', ');
    }
  }

  if (slider) {
    slider.addEventListener('input', () => update(+slider.value));
  }
  update(slider ? +slider.value : 1899);
}

function renderAdminNetwork(payload) {
  const container = document.getElementById('admin-network-svg');
  if (!container || !payload || !payload.nodes || !payload.nodes.length) return;
  if (typeof d3 === 'undefined') {
    console.warn('d3 not loaded');
    return;
  }

  container.innerHTML = '';
  const rect = container.getBoundingClientRect();
  const width = rect.width || 880;
  const height = rect.height || 560;

  const tooltip = document.createElement('div');
  tooltip.className = 'admin-tooltip';
  container.appendChild(tooltip);

  const svg = d3.select(container)
    .append('svg')
    .attr('viewBox', `0 0 ${width} ${height}`);

  // Arrowhead marker
  svg.append('defs').selectAll('marker')
    .data(['reports_to', 'employed_by'])
    .join('marker')
      .attr('id', d => `arrow-${d}`)
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 18)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
        .attr('d', 'M0,-5L10,0L0,5')
        .attr('fill', d => d === 'reports_to' ? '#2c6e85' : '#b87333');

  // Work on copies — d3.force mutates
  const nodes = payload.nodes.map(n => Object.assign({}, n));
  const edges = payload.edges.map(e => Object.assign({}, e));

  const nodeById = new Map(nodes.map(n => [n.id, n]));
  const radiusFor = n =>
    n.is_department ? 28
    : 6 + 2 * Math.sqrt(n.in_degree + n.out_degree);

  // Pin the department hub to the centre so the graph can't drift.
  nodes.forEach(n => {
    if (n.is_department) {
      n.fx = width / 2;
      n.fy = height / 2;
    }
  });

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges)
      .id(d => d.id)
      .distance(e => e.predicate === 'employed_by' && e.target.is_department ? 180 : 90)
      .strength(e => e.synthetic ? 0.15 : 0.7))
    .force('charge', d3.forceManyBody().strength(-320))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('x', d3.forceX(width / 2).strength(0.05))
    .force('y', d3.forceY(height / 2).strength(0.08))
    .force('collide', d3.forceCollide().radius(d => radiusFor(d) + 12));

  const link = svg.append('g')
    .selectAll('path')
    .data(edges)
    .join('path')
      .attr('class', e => `admin-edge ${e.predicate}${e.synthetic ? ' synthetic' : ''}`)
      .attr('marker-end', e => e.synthetic ? null : `url(#arrow-${e.predicate})`)
      .attr('stroke-width', e => e.synthetic ? 1 : 1.5 + Math.log2(1 + e.count))
      .on('mousemove', (ev, e) => {
        const sourceLabel = nodeById.get(e.source.id || e.source)?.label || '';
        const targetLabel = nodeById.get(e.target.id || e.target)?.label || '';
        const years = e.years ? (e.years[0] === e.years[1] ? e.years[0] : `${e.years[0]}–${e.years[1]}`) : '';
        tooltip.innerHTML =
          `<strong>${sourceLabel}</strong> — ${e.predicate.replace('_', ' ')} → <strong>${targetLabel}</strong><br>` +
          `${years} · ${e.count} mention${e.count === 1 ? '' : 's'}` +
          (e.evidence_sample ? `<div class="evidence">"${e.evidence_sample}"</div>` : '');
        tooltip.style.display = 'block';
        tooltip.style.left = (ev.offsetX + 14) + 'px';
        tooltip.style.top  = (ev.offsetY + 14) + 'px';
      })
      .on('mouseleave', () => { tooltip.style.display = 'none'; });

  const node = svg.append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
      .attr('class', n =>
        'admin-node ' +
        (n.is_department ? 'department' : (n.grounded_uri ? 'grounded' : 'ungrounded')))
      .call(d3.drag()
        .on('start', (ev, d) => {
          if (d.is_department) return;  // hub stays pinned
          if (!ev.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on('drag',  (ev, d) => { if (d.is_department) return; d.fx = ev.x; d.fy = ev.y; })
        .on('end',   (ev, d) => {
          if (d.is_department) return;
          if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null;
        }));

  node.append('circle').attr('r', radiusFor);

  node.append('text')
    .attr('x', n => n.is_department ? 0 : radiusFor(n) + 4)
    .attr('y', n => n.is_department ? 4 : 4)
    .attr('text-anchor', n => n.is_department ? 'middle' : 'start')
    .text(n => n.is_department
      ? 'Dept. of Indian Affairs'
      : n.label.replace(/,.*$/, '').replace(/\s+(Esq\.?|M\.?D\.?)$/i, ''));

  node.on('mousemove', (ev, n) => {
    const years = n.years ? `${n.years[0]}–${n.years[1]}` : '';
    tooltip.innerHTML =
      `<strong>${n.label}</strong><br>` +
      `in ${n.in_degree} · out ${n.out_degree} · ${n.section_count} section${n.section_count === 1 ? '' : 's'} · ${years}` +
      (n.grounded_uri ? `<div class="evidence">${n.grounded_uri}</div>` : '<div class="evidence">ungrounded</div>');
    tooltip.style.display = 'block';
    tooltip.style.left = (ev.offsetX + 14) + 'px';
    tooltip.style.top  = (ev.offsetY + 14) + 'px';
  }).on('mouseleave', () => { tooltip.style.display = 'none'; });

  sim.on('tick', () => {
    // Clamp each node so it can't float off-screen.
    nodes.forEach(d => {
      if (d.is_department) return;
      const r = radiusFor(d) + 4;
      d.x = Math.max(r, Math.min(width - r, d.x));
      d.y = Math.max(r, Math.min(height - r, d.y));
    });
    link.attr('d', e => `M${e.source.x},${e.source.y}L${e.target.x},${e.target.y}`);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });
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
      const nb = document.querySelector(`.case-neighborhood[data-agent="${agent}"]`);
      if (nb) renderNeighborhood(nb, a.neighborhood);
      renderMap(`map-${agent}`, a.places || [], a.map_note);
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

  // Place-map panel
  try {
    const placeMap = await loadJSON('data/place-map.json');
    renderPlaceMap(placeMap);
  } catch (e) {
    console.error('Place map:', e);
  }

  // Admin network panel
  try {
    const adminNet = await loadJSON('data/bc-admin-network.json');
    renderAdminNetwork(adminNet);
  } catch (e) {
    console.error('Admin network:', e);
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
        `<strong>Method:</strong> of ${m.total_sections_considered} sections in 1880–1899, ` +
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
