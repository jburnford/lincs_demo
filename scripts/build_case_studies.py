#!/usr/bin/env python3
"""
Build pre-rendered case study payloads for the demo page.

For each of the three case study agents (Dewdney, Powell, Boucher):
  - Query LINCS for the agent's occupation history (years, roles, places)
  - Walk our 1880-1885 sections and collect every year + section_id
    where the agent is mentioned
  - Pull a short snippet from the section markdown around the name
  - Fetch lat/lon for the agent's LINCS places (from LINCS P168 WKT)

Output: data/agent-timelines.json consumed by assets/demo.js.
"""

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_ttl import LincsAgentIndex, surname_initial


AGENTS = {
    'dewdney': {
        'lincs_uri': 'http://viaf.org/viaf/20583753',
        'canonical_name': 'Edgar Dewdney',
        'match_names': ['Dewdney'],
        'blurb': (
            'Indian Commissioner of the North-West Territories (1879–1888) '
            'and Lieutenant-Governor of the NWT (1881–1888). A central '
            'figure in Canadian Indigenous policy during the crisis years '
            'that culminated in the 1885 North-West Resistance.'
        ),
    },
    'powell': {
        'lincs_uri': 'http://id.lincsproject.ca/4wjfC8s424E',
        'canonical_name': 'Israel Wood Powell',
        'match_names': ['Powell'],
        'blurb': (
            'Superintendent of Indian Affairs for British Columbia '
            '(1872–1889). Based in Victoria, Powell administered the '
            'province during the treaty-less period of BC Indigenous '
            'policy — a striking contrast to the treaty-focused '
            'Prairies.'
        ),
    },
    'boucher': {
        'lincs_uri': 'http://id.lincsproject.ca/Flj4VecmJa5',
        'canonical_name': 'L.F. Boucher',
        'match_names': ['Boucher'],
        'blurb': (
            'Indian Agent at Betsiamites on the Lower North Shore of the '
            'St. Lawrence, serving the Innu of the Côte-Nord. LINCS '
            'stores this agent under two separate names — "L.F. Boucher" '
            'and "Louis Boucher" — which this pipeline resolves to a '
            'single career.'
        ),
        'also_merge': ['http://id.lincsproject.ca/Dut48Z2iAM7'],
    },
}


LINCS_ENDPOINT = 'https://fuseki.lincsproject.ca/lincs/sparql'


def sparql(query: str) -> dict:
    req = urllib.request.Request(
        LINCS_ENDPOINT,
        data=query.encode('utf-8'),
        headers={
            'Content-Type': 'application/sparql-query',
            'Accept': 'application/sparql-results+json',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fetch_place_coords(place_uris: list[str]) -> dict[str, dict]:
    """Return {uri: {lat, lon, label}} for given place URIs via LINCS."""
    if not place_uris:
        return {}
    values = ' '.join(f'<{u}>' for u in place_uris)
    q = f"""
PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?place ?label ?wkt WHERE {{
  VALUES ?place {{ {values} }}
  GRAPH <http://graph.lincsproject.ca/hist-canada/ind-affairs> {{
    ?place rdfs:label ?label .
    OPTIONAL {{ ?place crm:P168_place_is_defined_by ?wkt . }}
  }}
}}
"""
    data = sparql(q)
    out: dict[str, dict] = {}
    for r in data['results']['bindings']:
        uri = r['place']['value']
        label = r['label']['value']
        if uri in out and ',' in label:
            # Prefer shorter labels without province suffix
            continue
        entry = out.get(uri, {'label': label})
        if len(label) < len(entry.get('label', label)):
            entry['label'] = label
        if 'wkt' in r and 'lat' not in entry:
            m = re.match(r'POINT\(([-\d.]+)\s+([-\d.]+)\)', r['wkt']['value'])
            if m:
                entry['lon'] = float(m.group(1))
                entry['lat'] = float(m.group(2))
        out[uri] = entry
    return out


def fetch_agent_history(uri: str) -> list[dict]:
    """Return a list of {role, place_uri, place_label, begin, end} for an agent."""
    q = f"""
PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX event: <http://id.lincsproject.ca/event/>
SELECT DISTINCT ?occLabel ?place ?placeLabel ?begin ?end WHERE {{
  GRAPH <http://graph.lincsproject.ca/hist-canada/ind-affairs> {{
    ?occ a crm:E7_Activity ;
         crm:P14_carried_out_by <{uri}> ;
         crm:P2_has_type event:OccupationEvent ;
         rdfs:label ?occLabel ;
         crm:P4_has_time-span ?ts .
    ?ts crm:P82a_begin_of_the_begin ?begin .
    OPTIONAL {{ ?ts crm:P82b_end_of_the_end ?end . }}
    OPTIONAL {{ ?occ crm:P7_took_place_at ?place . ?place rdfs:label ?placeLabel . }}
  }}
}}
ORDER BY ?begin
"""
    data = sparql(q)
    out = []
    for r in data['results']['bindings']:
        out.append({
            'role_label': r['occLabel']['value'],
            'place_uri': r.get('place', {}).get('value'),
            'place_label': r.get('placeLabel', {}).get('value'),
            'begin': r['begin']['value'][:4],
            'end': r.get('end', {}).get('value', '')[:4] or None,
        })
    return out


def extract_snippet(text: str, name: str, window: int = 180) -> str | None:
    """Find a passage containing the name and return ~window chars around it."""
    if not text:
        return None
    # Surname-based search (case-insensitive), grab a tight window.
    surname = max(name.split(), key=len)
    pattern = re.compile(rf'\b{re.escape(surname)}\b', re.I)
    m = pattern.search(text)
    if not m:
        return None
    start = max(0, m.start() - window // 2)
    end = min(len(text), m.end() + window // 2)
    snippet = text[start:end].strip()
    # Clean newlines and markdown cruft
    snippet = re.sub(r'\s+', ' ', snippet)
    snippet = re.sub(r'[*#_]{2,}', '', snippet)
    if start > 0:
        snippet = '…' + snippet
    if end < len(text):
        snippet = snippet + '…'
    return snippet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entities', type=Path,
                    default=Path.home() / 'plato/dia_data/entities_raw')
    ap.add_argument('--sections', type=Path,
                    default=Path.home() / 'plato/dia_data/sections')
    ap.add_argument('--lincs', type=Path,
                    default=Path('data/lincs_agents_raw.json'))
    ap.add_argument('--out', type=Path,
                    default=Path('data/agent-timelines.json'))
    ap.add_argument('--years', type=int, nargs=2, default=[1880, 1885])
    args = ap.parse_args()

    idx = LincsAgentIndex(args.lincs)

    # Pre-load all section texts for the years of interest
    all_sections: dict[str, dict] = {}
    for year in range(args.years[0], args.years[1] + 1):
        sec_fp = args.sections / f"Indian Affairs AR {year}.jsonl"
        if not sec_fp.exists():
            continue
        for line in sec_fp.read_text().splitlines():
            if not line.strip():
                continue
            s = json.loads(line)
            all_sections[s['section_id']] = s

    payload: dict = {}

    for key, cfg in AGENTS.items():
        print(f'\n=== {key}: {cfg["canonical_name"]} ===')
        uris = [cfg['lincs_uri']] + cfg.get('also_merge', [])

        # LINCS occupation history across all merged URIs
        history: list[dict] = []
        place_uris_all: set[str] = set()
        for u in uris:
            h = fetch_agent_history(u)
            print(f'  {len(h)} LINCS occupation rows from {u}')
            history.extend(h)
            for row in h:
                if row.get('place_uri'):
                    place_uris_all.add(row['place_uri'])

        # Fetch coordinates for all the places
        place_info = fetch_place_coords(list(place_uris_all))
        print(f'  {sum(1 for p in place_info.values() if "lat" in p)} / {len(place_info)} places have coordinates')

        # Walk our sections to find where the agent is mentioned (1880-1885)
        mentions_by_year: dict[int, list[dict]] = defaultdict(list)
        match_names_lc = [n.lower() for n in cfg['match_names']]
        for year in range(args.years[0], args.years[1] + 1):
            ent_fp = args.entities / f"Indian Affairs AR {year}.jsonl"
            if not ent_fp.exists():
                continue
            for line in ent_fp.read_text().splitlines():
                if not line.strip():
                    continue
                ent = json.loads(line)
                if ent.get('skipped'):
                    continue

                # Does any extracted person in this section match?
                matched = False
                matched_role = None
                matched_name = None
                for p in ent.get('persons') or []:
                    nm = p.get('name', '')
                    if not nm:
                        continue
                    nm_lc = nm.lower()
                    if any(mn in nm_lc for mn in match_names_lc):
                        # Confirm via LINCS grounding for robustness
                        grounded = idx.match_person(nm, year)
                        if grounded in uris:
                            matched = True
                            matched_role = p.get('role')
                            matched_name = nm
                            break
                if not matched:
                    continue

                sec = all_sections.get(ent['section_id'], {})
                mentions_by_year[year].append({
                    'section_id': ent['section_id'],
                    'agency': ent.get('agency'),
                    'role_in_text': matched_role,
                    'name_in_text': matched_name,
                    'text': sec.get('text', ''),
                })

        # Preferred role ranking — pick the most senior/relevant title when
        # LINCS records multiple roles for the same year.
        ROLE_PRIORITY = [
            'Indian Commissioner', 'Lieutenant-Governor', 'Superintendent',
            'Indian Superintendent', 'Inspector', 'Indian Agent', 'Agent',
            'Farming Instructor', 'Medical Officer', 'Interpreter', 'Clerk',
        ]
        def role_rank(label: str):
            for i, kw in enumerate(ROLE_PRIORITY):
                if kw.lower() in label.lower():
                    # Secondary key: prefer shorter labels ("Agent" over
                    # "Agent provisionally") and simpler ones.
                    return (i, len(label))
            return (len(ROLE_PRIORITY), len(label))

        # Build the timeline (one entry per year with a mention)
        # Filter LINCS history to our demo window to avoid out-of-window noise.
        y0, y1 = args.years
        history_in_window = [
            h for h in history
            if h.get('begin') and int(h['begin']) <= y1
               and (not h.get('end') or int(h['end']) >= y0)
        ]

        timeline = []
        for year in sorted(mentions_by_year.keys()):
            ms = mentions_by_year[year]
            first = ms[0]
            role_in_text = first['role_in_text'] or ''
            active = [
                h for h in history_in_window
                if int(h['begin']) <= year
                   and (not h.get('end') or int(h['end']) >= year)
            ]
            lincs_roles = sorted(
                {h['role_label'].split(' occupation of')[0] for h in active},
                key=role_rank,
            )
            place_labels = sorted({
                h['place_label']
                for h in active
                if h.get('place_label') and ',' not in h['place_label']
            })
            timeline.append({
                'year': year,
                'summary': f'{len(ms)} section{"s" if len(ms) != 1 else ""} in the Annual Report',
                'role': lincs_roles[0] if lincs_roles else role_in_text,
                'place': place_labels[0] if place_labels else None,
                'n_mentions': len(ms),
            })

        # Build snippets — pick up to 3, spread across years if possible
        snippets = []
        picked_years = set()
        for year in sorted(mentions_by_year.keys()):
            if len(snippets) >= 3:
                break
            if year in picked_years:
                continue
            for m in mentions_by_year[year]:
                sn = extract_snippet(m['text'], m['name_in_text'] or cfg['canonical_name'])
                if sn and len(sn) > 60:
                    snippets.append({
                        'text': sn,
                        'source': f'DIA Annual Report {year} · {m.get("agency") or m["section_id"]}',
                    })
                    picked_years.add(year)
                    break

        # Places for the map — only those active in the demo window,
        # dedupe by coordinate.
        places_out = []
        seen_coords: set[tuple] = set()
        for uri, info in place_info.items():
            if 'lat' not in info:
                continue
            # Years this place was active in our window
            active_years = [
                y for y in range(args.years[0], args.years[1] + 1)
                if any(
                    h.get('place_uri') == uri
                    and h.get('begin') and int(h['begin']) <= y
                    and (not h.get('end') or int(h['end']) >= y)
                    for h in history
                )
            ]
            if not active_years:
                continue  # place not active in our window — skip
            coord = (round(info['lat'], 3), round(info['lon'], 3))
            if coord in seen_coords:
                continue
            seen_coords.add(coord)
            year_str = ''
            if active_years:
                year_str = f'{active_years[0]}–{active_years[-1]}' if len(active_years) > 1 else str(active_years[0])
            places_out.append({
                'uri': uri,
                'name': info['label'],
                'lat': info['lat'],
                'lon': info['lon'],
                'years': year_str,
            })

        payload[key] = {
            'name': cfg['canonical_name'],
            'lincs_uri': cfg['lincs_uri'],
            'blurb': cfg['blurb'],
            'timeline': timeline,
            'snippets': snippets,
            'places': places_out,
            'n_mentions_total': sum(len(ms) for ms in mentions_by_year.values()),
        }
        print(f'  timeline: {len(timeline)} years | snippets: {len(snippets)} | places: {len(places_out)} | total mentions: {payload[key]["n_mentions_total"]}')

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f'\nWrote {args.out}')


if __name__ == '__main__':
    main()
