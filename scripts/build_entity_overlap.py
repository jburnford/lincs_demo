#!/usr/bin/env python3
"""
Cross-agent entity overlap analysis for the BC knowledge graph.

For each entity (indigenous group, place, school), determine which
BC-serving agents have sections that mention it. Entities named by
multiple agents reveal jurisdictional overlap, shared territory, and
the Reserve Commissioner's province-wide reach.

Output: data/bc-entity-overlap.json
  {
    "top_bands": [
      { "name", "agent_count", "total_mentions", "agents": [{uri, name, count}] }
    ],
    "top_places": [...],
    "top_schools": [...],
    "stats": {...}
  }
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


SCHOOL_RE = re.compile(r'\b(school|college|academy|seminary)\b', re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kg', type=Path, default=Path('data/bc-agents-kg.json'))
    ap.add_argument('--out', type=Path,
                    default=Path('data/bc-entity-overlap.json'))
    args = ap.parse_args()

    kg = json.loads(args.kg.read_text())

    # section_id → list of agent URIs grounded in it
    section_to_agents: dict[str, list[str]] = defaultdict(list)
    agent_meta: dict[str, dict] = {}
    for a in kg['agents']:
        agent_meta[a['uri']] = {
            'uri': a['uri'],
            'canonical': a['canonical_name'],
        }
        for sid in a['sections']:
            section_to_agents[sid].append(a['uri'])

    # Drop sections with too many BC agents matched — these are almost
    # always annual Superintendent-General summaries that list agents in
    # a roll-call and happen to pull in non-BC entities as noise. Three
    # or fewer grounded agents means it's a focused per-agency report.
    summary_sections = {
        sid for sid, uris in section_to_agents.items() if len(uris) > 3
    }
    section_to_agents = {
        sid: uris for sid, uris in section_to_agents.items()
        if sid not in summary_sections
    }
    print(f'Dropped {len(summary_sections)} summary sections with >3 BC agents')

    def accumulate(entity_list, school_only=False):
        """
        Build entity_name → {agents: {uri: count}, total: int}.
        Returns list sorted by (# distinct agents, total mentions).
        """
        per_entity: dict[str, dict] = {}
        for e in entity_list:
            nm = e['name']
            if school_only and not SCHOOL_RE.search(nm):
                continue
            key = nm.strip().lower()
            entry = per_entity.setdefault(key, {
                'name': nm,
                'agents_counts': defaultdict(int),
                'total_mentions': 0,
            })
            for m in e['mentions']:
                sid = m['section_id']
                if sid not in section_to_agents:
                    continue  # dropped summary section
                for uri in section_to_agents[sid]:
                    entry['agents_counts'][uri] += 1
                    entry['total_mentions'] += 1
        results = []
        for v in per_entity.values():
            agent_list = [
                {
                    'uri': uri,
                    'name': agent_meta[uri]['canonical'],
                    'count': c,
                }
                for uri, c in sorted(v['agents_counts'].items(), key=lambda x: -x[1])
            ]
            results.append({
                'name': v['name'],
                'agent_count': len(agent_list),
                'total_mentions': v['total_mentions'],
                'agents': agent_list,
            })
        results.sort(
            key=lambda r: (-r['agent_count'], -r['total_mentions'])
        )
        return results

    bands = accumulate(kg['entities']['indigenous_groups'])
    places = accumulate(kg['entities']['places_orgs'])
    schools = accumulate(kg['entities']['places_orgs'], school_only=True)

    # Keep only items named by ≥2 agents for the overlap report
    overlap_bands = [b for b in bands if b['agent_count'] >= 2]
    overlap_places = [p for p in places if p['agent_count'] >= 2]
    overlap_schools = [s for s in schools if s['agent_count'] >= 2]

    payload = {
        'top_bands': overlap_bands[:40],
        'top_places': overlap_places[:40],
        'top_schools': overlap_schools[:30],
        'stats': {
            'distinct_bands': len(bands),
            'bands_with_overlap': len(overlap_bands),
            'distinct_places': len(places),
            'places_with_overlap': len(overlap_places),
            'distinct_schools': len(schools),
            'schools_with_overlap': len(overlap_schools),
        },
    }
    args.out.write_text(json.dumps(payload, indent=2))

    print('Overlap stats:')
    for k, v in payload['stats'].items():
        print(f'  {k:30s} {v}')
    print()
    print('=== Bands named by the most agents ===')
    for b in overlap_bands[:12]:
        print(f'  {b["agent_count"]:>2} agents, {b["total_mentions"]:>3} mentions  {b["name"]}')
        for a in b['agents'][:4]:
            print(f'       {a["count"]:>2}x  {a["name"]}')
    print()
    print('=== Places named by the most agents ===')
    for p in overlap_places[:12]:
        print(f'  {p["agent_count"]:>2} agents, {p["total_mentions"]:>3} mentions  {p["name"]}')
        for a in p['agents'][:4]:
            print(f'       {a["count"]:>2}x  {a["name"]}')
    print(f'\nWrote {args.out}')


if __name__ == '__main__':
    main()
