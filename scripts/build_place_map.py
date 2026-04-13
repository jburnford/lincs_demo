#!/usr/bin/env python3
"""
Build the place-map payload for the demo (data/place-map.json).

Joins the section→place index (from match_places.py) with the LINCS place
inventory, extracts coordinates from the WKT, assigns a region using the
same rules as regional_tfidf.py, and aggregates mention counts per place
per year.

Output schema:
    {
      "years": [1880, 1899],
      "regions": {"bc": "British Columbia", "prairies": "Prairies / NWT", ...},
      "places": [
        {
          "uri": "https://sws.geonames.org/...",
          "label": "Battleford, Saskatchewan",
          "lat": 52.74,
          "lon": -108.30,
          "region": "prairies",
          "total_mentions": 137,
          "by_year": {"1880": 5, "1881": 12, ...},
          "section_count": 84
        },
        ...
      ]
    }
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from regional_tfidf import region_for_place_label  # noqa: E402


def extract_latlon(wkt: str | None) -> tuple[float, float] | None:
    if not wkt:
        return None
    m = re.match(r'\s*POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)', wkt)
    if not m:
        return None
    return float(m.group(2)), float(m.group(1))  # (lat, lon)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lincs-places', type=Path,
                    default=Path('data/lincs_places_raw.json'))
    ap.add_argument('--section-places', type=Path,
                    default=Path('data/section-places.json'))
    ap.add_argument('--out', type=Path,
                    default=Path('data/place-map.json'))
    args = ap.parse_args()

    # Build place meta: uri → {canonical, wkt, labels}
    raw = json.loads(args.lincs_places.read_text())
    place_meta: dict[str, dict] = {}
    for r in raw['results']['bindings']:
        uri = r['place']['value']
        lab = r['label']['value']
        entry = place_meta.setdefault(uri, {'labels': set(), 'wkt': None})
        entry['labels'].add(lab)
        if 'wkt' in r and not entry['wkt']:
            entry['wkt'] = r['wkt']['value']
    for e in place_meta.values():
        e['canonical'] = max(e['labels'], key=len)

    # Read section → places
    sections = json.loads(args.section_places.read_text())

    # Aggregate: uri → {by_year: Counter, section_ids: set}
    agg: dict[str, dict] = defaultdict(
        lambda: {'by_year': Counter(), 'section_ids': set()}
    )
    for s in sections:
        year = s['year']
        sid = s['section_id']
        for uri in s['place_uris']:
            agg[uri]['by_year'][year] += 1
            agg[uri]['section_ids'].add(sid)

    # Compose output
    places_out = []
    years_seen = set()
    no_coords = 0
    for uri, a in agg.items():
        meta = place_meta.get(uri, {})
        label = meta.get('canonical', uri)
        latlon = extract_latlon(meta.get('wkt'))
        if not latlon:
            no_coords += 1
            continue
        lat, lon = latlon
        region = region_for_place_label(label) or 'other'
        total = sum(a['by_year'].values())
        places_out.append({
            'uri': uri,
            'label': label,
            'lat': round(lat, 5),
            'lon': round(lon, 5),
            'region': region,
            'total_mentions': total,
            'by_year': {str(y): c for y, c in sorted(a['by_year'].items())},
            'section_count': len(a['section_ids']),
        })
        years_seen |= set(a['by_year'].keys())

    places_out.sort(key=lambda p: -p['total_mentions'])

    payload = {
        'years': [min(years_seen), max(years_seen)] if years_seen else [None, None],
        'regions': {
            'bc': 'British Columbia',
            'prairies': 'Prairies / North-West Territories',
            'quebec': 'Quebec',
            'other': 'Other (Ontario, Maritimes, etc.)',
        },
        'places': places_out,
        'stats': {
            'total_distinct_places': len(places_out),
            'total_mentions': sum(p['total_mentions'] for p in places_out),
            'dropped_no_coords': no_coords,
            'by_region': dict(Counter(p['region'] for p in places_out)),
        },
    }
    args.out.write_text(json.dumps(payload, indent=2))

    print(f"Distinct places with coords: {len(places_out)}")
    print(f"Dropped (no WKT): {no_coords}")
    print(f"Total mentions plotted: {payload['stats']['total_mentions']}")
    print(f"By region: {payload['stats']['by_region']}")
    print(f"Wrote {args.out}")


if __name__ == '__main__':
    main()
