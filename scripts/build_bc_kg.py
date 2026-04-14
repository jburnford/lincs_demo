#!/usr/bin/env python3
"""
Build a basic knowledge graph anchored on BC-serving Indian Agents.

Starting point: every LINCS agent whose occupation places include
"British Columbia" (strict literal). For each such agent, walk the
1880-1899 NER output, find sections that ground to that agent and
aren't tagged to a contradicting province, and collect every extracted
entity and relationship from those sections.

Output: data/bc-agents-kg.json
  {
    "agents": [                   # 82 BC-serving LINCS agents
      { "uri", "canonical_name", "places": [...], "sections": [sid, ...] }
    ],
    "entities": {
      "persons":         [ { "name", "role", "affiliation", "mentions": [{sid, year}] } ],
      "indigenous":      [ { "name", "type", "reserve", "location", "mentions": [...] } ],
      "places_orgs":     [ { "name", "type", "category", "location", "lincs_uri"?, "mentions": [...] } ],
      "events":          [ { "name", "date", "location", "mentions": [...] } ]
    },
    "relationships": [            # flat triples with section provenance
      { "subject", "predicate", "object", "evidence", "section_id", "year",
        "subject_grounded": "uri or null", "object_grounded": "uri or null" }
    ],
    "stats": {...}
  }

Ungrounded entities are kept as bare strings (no OCAP concerns for
non-Indigenous entities; Indigenous groups are preserved as extracted
text only, not linked to external authorities).
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_ttl import LincsAgentIndex  # noqa: E402
from match_places import LincsPlaceIndex  # noqa: E402


# Province-field regex: section is in a non-BC province → drop.
NON_BC_PROVINCE = re.compile(
    r'\b(ontario|quebec|manitoba|saskatchewan|alberta|'
    r'nova\s*scotia|new\s*brunswick|prince\s*edward|'
    r'north-?west\s*territor|nwt|maritimes?)\b',
    re.I,
)


def section_is_bc_compatible(province_field: str | None) -> bool:
    if not province_field:
        return True
    return not NON_BC_PROVINCE.search(province_field)


def normalise_name(n: str) -> str:
    return re.sub(r'\s+', ' ', (n or '').strip().lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entities', type=Path,
                    default=Path.home() / 'plato/dia_data/entities_raw')
    ap.add_argument('--agents', type=Path,
                    default=Path('data/lincs_agents_raw.json'))
    ap.add_argument('--places', type=Path,
                    default=Path('data/lincs_places_raw.json'))
    ap.add_argument('--out', type=Path,
                    default=Path('data/bc-agents-kg.json'))
    ap.add_argument('--years', type=int, nargs=2, default=[1880, 1899])
    args = ap.parse_args()

    agent_idx = LincsAgentIndex(args.agents)
    place_idx = LincsPlaceIndex(args.places)

    # Strict BC agent set
    bc_agents: dict[str, dict] = {}
    for uri, e in agent_idx.by_uri.items():
        bc_labels = sorted(
            lbl for lbl in e['place_labels']
            if 'british columbia' in lbl.lower()
        )
        if bc_labels:
            canonical = max(e['names'], key=len) if e['names'] else uri
            bc_agents[uri] = {
                'uri': uri,
                'canonical_name': canonical,
                'names': sorted(e['names']),
                'places': bc_labels,
                'sections': set(),
            }
    print(f'BC-serving LINCS agents: {len(bc_agents)}')

    # Per-agent name → URI for relationship grounding
    name_to_uri: dict[str, str] = {}
    for uri, info in bc_agents.items():
        for nm in info['names']:
            name_to_uri[normalise_name(nm)] = uri

    # Aggregators
    persons_agg: dict[tuple, dict] = {}      # (name_norm) → record
    indig_agg: dict[tuple, dict] = {}
    placesorgs_agg: dict[tuple, dict] = {}
    events_agg: dict[tuple, dict] = {}
    relationships: list[dict] = []

    n_sections_considered = 0
    n_sections_kept = 0
    n_sections_dropped_province = 0

    for year in range(args.years[0], args.years[1] + 1):
        fp = args.entities / f"Indian Affairs AR {year}.jsonl"
        if not fp.exists():
            continue
        for line in fp.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            section_id = rec.get('section_id')
            if not section_id:
                continue

            # Ground persons; find BC-agent hits
            matched_bc_uris = set()
            for p in rec.get('persons') or []:
                uri = agent_idx.match_person(p.get('name') or '', year)
                if uri and uri in bc_agents:
                    matched_bc_uris.add(uri)

            if not matched_bc_uris:
                continue
            n_sections_considered += 1

            # Province sanity check
            if not section_is_bc_compatible(rec.get('province')):
                n_sections_dropped_province += 1
                continue
            n_sections_kept += 1

            for uri in matched_bc_uris:
                bc_agents[uri]['sections'].add(section_id)

            # Entity aggregation
            for p in rec.get('persons') or []:
                nm = (p.get('name') or '').strip()
                if not nm:
                    continue
                key = normalise_name(nm)
                entry = persons_agg.setdefault(key, {
                    'name': nm,
                    'role': p.get('role'),
                    'affiliation': p.get('affiliation'),
                    'mentions': [],
                    'grounded_uri': name_to_uri.get(key),
                })
                entry['mentions'].append({'section_id': section_id, 'year': year})

            for g in rec.get('indigenous_groups') or []:
                nm = (g.get('name') or '').strip()
                if not nm:
                    continue
                key = normalise_name(nm)
                entry = indig_agg.setdefault(key, {
                    'name': nm,
                    'type': g.get('type'),
                    'reserve_name': g.get('reserve_name'),
                    'location': g.get('location'),
                    'population': g.get('population'),
                    'mentions': [],
                })
                entry['mentions'].append({'section_id': section_id, 'year': year})

            for pl in rec.get('places_orgs') or []:
                nm = (pl.get('name') or '').strip()
                if not nm:
                    continue
                key = normalise_name(nm)
                # Try LINCS place grounding (only for PLACE category)
                lincs_uri = None
                if pl.get('category') == 'PLACE':
                    lincs_uri = place_idx.lookup(nm, pl.get('location'))
                entry = placesorgs_agg.setdefault(key, {
                    'name': nm,
                    'type': pl.get('type'),
                    'category': pl.get('category'),
                    'location': pl.get('location'),
                    'lincs_uri': lincs_uri,
                    'mentions': [],
                })
                entry['mentions'].append({'section_id': section_id, 'year': year})

            for ev in rec.get('events') or []:
                nm = (ev.get('name') or '').strip()
                if not nm:
                    continue
                key = normalise_name(nm) + '|' + (ev.get('date') or '')
                entry = events_agg.setdefault(key, {
                    'name': nm,
                    'date': ev.get('date'),
                    'location': ev.get('location'),
                    'mentions': [],
                })
                entry['mentions'].append({'section_id': section_id, 'year': year})

            for r in rec.get('relationships') or []:
                subj = (r.get('subject') or '').strip()
                obj = (r.get('object') or '').strip()
                pred = (r.get('predicate') or '').strip()
                if not (subj and pred and obj):
                    continue
                relationships.append({
                    'subject': subj,
                    'predicate': pred,
                    'object': obj,
                    'evidence': r.get('evidence'),
                    'section_id': section_id,
                    'year': year,
                    'subject_grounded': name_to_uri.get(normalise_name(subj)),
                    'object_grounded': name_to_uri.get(normalise_name(obj)),
                })

    # Finalize
    for info in bc_agents.values():
        info['sections'] = sorted(info['sections'])
    agents_out = [
        info for info in bc_agents.values() if info['sections']
    ]
    agents_out.sort(key=lambda a: -len(a['sections']))

    def agg_to_list(agg):
        out = list(agg.values())
        out.sort(key=lambda e: -len(e['mentions']))
        return out

    # Stats
    pred_counts: dict[str, int] = defaultdict(int)
    for r in relationships:
        pred_counts[r['predicate'].lower()] += 1
    top_preds = sorted(pred_counts.items(), key=lambda x: -x[1])[:30]

    payload = {
        'years': args.years,
        'agents': agents_out,
        'entities': {
            'persons': agg_to_list(persons_agg),
            'indigenous_groups': agg_to_list(indig_agg),
            'places_orgs': agg_to_list(placesorgs_agg),
            'events': agg_to_list(events_agg),
        },
        'relationships': relationships,
        'stats': {
            'bc_agents_total': len(bc_agents),
            'bc_agents_in_reports': len(agents_out),
            'sections_considered': n_sections_considered,
            'sections_kept': n_sections_kept,
            'sections_dropped_non_bc_province': n_sections_dropped_province,
            'entity_counts': {
                'persons': len(persons_agg),
                'indigenous_groups': len(indig_agg),
                'places_orgs': len(placesorgs_agg),
                'events': len(events_agg),
            },
            'relationships_total': len(relationships),
            'relationships_subject_grounded': sum(1 for r in relationships if r['subject_grounded']),
            'relationships_object_grounded': sum(1 for r in relationships if r['object_grounded']),
            'top_predicates': top_preds,
        },
    }
    args.out.write_text(json.dumps(payload, indent=2, default=str))

    s = payload['stats']
    print(f"\nSections considered (≥1 BC agent matched): {s['sections_considered']}")
    print(f"Sections dropped (non-BC province tag):     {s['sections_dropped_non_bc_province']}")
    print(f"Sections kept:                              {s['sections_kept']}")
    print(f"\nBC agents appearing in reports: {s['bc_agents_in_reports']} / {s['bc_agents_total']}")
    print(f"Relationships:                  {s['relationships_total']}")
    print(f"  subject grounded to BC agent: {s['relationships_subject_grounded']}")
    print(f"  object grounded to BC agent:  {s['relationships_object_grounded']}")
    print(f"\nDistinct entities:")
    for k, v in s['entity_counts'].items():
        print(f"  {k:20s} {v}")
    print(f"\nTop predicates:")
    for pred, n in top_preds[:15]:
        print(f"  {n:>5}  {pred}")
    print(f"\nTop 10 agents by section count:")
    for a in agents_out[:10]:
        print(f"  {len(a['sections']):>3}  {a['canonical_name']}")
    print(f"\nWrote {args.out}")


if __name__ == '__main__':
    main()
