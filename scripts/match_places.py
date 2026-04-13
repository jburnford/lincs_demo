#!/usr/bin/env python3
"""
Strict NER → LINCS place matcher.

Reads the LINCS Indian Affairs place inventory (lincs_places_raw.json) and
the per-year NER jsonl files (entities_raw/). Emits:

  - data/dia-places-1880-1899.ttl
      CIDOC-CRM Turtle: each matched place mention is a section-level
      P67_refers_to → LINCS place URI, with owl:sameAs to GeoNames where
      available.

  - data/place-matches.json
      audit output: match count per year, top unmatched mentions,
      colliding keys that were dropped, and URIs blacklisted for
      conflicting province labels.

Matching rules (designed to produce zero false positives for a public
demo):

  1. A LINCS place URI is *blacklisted* if its label variants contain
     more than one distinct "province token" (the last comma-segment
     after normalisation). Example: Victoria → {"British Columbia",
     "Northwest Territories, Manitoba, and Keewatin"} → blacklisted.

  2. The label index maps normalised(label_variant) → set of URIs.
     Keys mapping to >1 URI are dropped (collisions).

  3. For each NER place mention with category=PLACE, we build candidate
     keys in decreasing specificity:
        a.  normalise(name + ", " + location)   if location present
        b.  normalise(name)
     The first key that hits exactly one non-blacklisted URI wins.
     Otherwise the mention is unmatched.

Usage:
    python3 scripts/match_places.py \
        --entities ~/plato/dia_data/entities_raw \
        --lincs data/lincs_places_raw.json \
        --ttl data/dia-places-1880-1899.ttl \
        --audit data/place-matches.json \
        --years 1880 1899
"""

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_ABBREV = {
    'st': 'saint',
    'ste': 'sainte',
    'mt': 'mount',
    'ft': 'fort',
}

_PROVINCE_ALIASES = {
    'ont': 'ontario',
    'que': 'quebec',
    'qc': 'quebec',
    'bc': 'british columbia',
    'b c': 'british columbia',
    'nb': 'new brunswick',
    'ns': 'nova scotia',
    'pei': 'prince edward island',
    'p e i': 'prince edward island',
    'man': 'manitoba',
    'mb': 'manitoba',
    'sask': 'saskatchewan',
    'sk': 'saskatchewan',
    'alta': 'alberta',
    'ab': 'alberta',
    'nwt': 'northwest territories',
    'n w t': 'northwest territories',
    'north west territories': 'northwest territories',
    'north-west territories': 'northwest territories',
}


def _strip_accents(s: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def normalise(label: str) -> str:
    """Case-fold, strip accents/punct, expand abbreviations, collapse ws."""
    if not label:
        return ''
    s = _strip_accents(label).lower()
    # Drop parentheticals
    s = re.sub(r'\([^)]*\)', ' ', s)
    # Collapse possessive 's → s so "St. Peter's" and "St. Peters" match
    s = re.sub(r"'s\b", 's', s)
    # Replace punctuation (keep commas as segment separators)
    s = re.sub(r'[^\w,\s-]', ' ', s)
    # Split on commas for per-segment abbreviation expansion
    parts = []
    for seg in s.split(','):
        toks = re.split(r'[\s\-]+', seg.strip())
        toks = [t for t in toks if t]
        # Drop leading articles
        if toks and toks[0] == 'the':
            toks = toks[1:]
        # Expand abbreviations token-by-token
        expanded = []
        for t in toks:
            t2 = _ABBREV.get(t, t)
            expanded.append(t2)
        seg_out = ' '.join(expanded).strip()
        # Province alias (whole segment)
        seg_out = _PROVINCE_ALIASES.get(seg_out, seg_out)
        if seg_out:
            parts.append(seg_out)
    return ', '.join(parts)


def province_token(norm: str) -> str | None:
    """Return the last comma-segment if it looks like a province, else None."""
    if ',' not in norm:
        return None
    tail = norm.rsplit(',', 1)[1].strip()
    # Known province set
    known = {
        'ontario', 'quebec', 'british columbia', 'new brunswick', 'nova scotia',
        'prince edward island', 'manitoba', 'saskatchewan', 'alberta',
        'northwest territories', 'keewatin', 'yukon', 'newfoundland',
    }
    # Also handle compound tails ("manitoba and keewatin")
    for k in known:
        if k in tail:
            return k
    return None


def all_province_tokens(norm: str) -> set[str]:
    """Return every province token appearing anywhere in the label tail."""
    known = {
        'ontario', 'quebec', 'british columbia', 'new brunswick', 'nova scotia',
        'prince edward island', 'manitoba', 'saskatchewan', 'alberta',
        'northwest territories', 'keewatin', 'yukon', 'newfoundland',
    }
    if ',' not in norm:
        return set()
    tail = norm.split(',', 1)[1]
    return {k for k in known if k in tail}


# ---------------------------------------------------------------------------
# LINCS place index
# ---------------------------------------------------------------------------

class LincsPlaceIndex:
    def __init__(self, raw_json_path: Path):
        data = json.loads(raw_json_path.read_text())
        self.by_uri: dict[str, dict] = {}
        for r in data['results']['bindings']:
            uri = r['place']['value']
            label = r['label']['value']
            entry = self.by_uri.setdefault(uri, {
                'labels': set(),
                'wkt': None,
                'sameAs': set(),
            })
            entry['labels'].add(label)
            if 'wkt' in r and not entry['wkt']:
                entry['wkt'] = r['wkt']['value']
            if 'sameAs' in r:
                entry['sameAs'].add(r['sameAs']['value'])

        # Compute canonical label (longest variant — usually most qualified)
        for uri, e in self.by_uri.items():
            e['canonical'] = max(e['labels'], key=len)

        # Blacklist URIs with conflicting province tokens across variants
        self.blacklist: set[str] = set()
        self.blacklist_reasons: dict[str, list[str]] = {}
        for uri, e in self.by_uri.items():
            seen = set()
            for lab in e['labels']:
                seen |= all_province_tokens(normalise(lab))
            if len(seen) > 1:
                self.blacklist.add(uri)
                self.blacklist_reasons[uri] = sorted(seen)

        # Per-URI province token (post-blacklist, so at most one).
        self.uri_province: dict[str, str | None] = {}
        for uri, e in self.by_uri.items():
            if uri in self.blacklist:
                continue
            provs = set()
            for lab in e['labels']:
                provs |= all_province_tokens(normalise(lab))
            self.uri_province[uri] = next(iter(provs)) if len(provs) == 1 else None

        # Build normalised label → {uris}
        self.key_index: dict[str, set[str]] = defaultdict(set)
        for uri, e in self.by_uri.items():
            if uri in self.blacklist:
                continue
            for lab in e['labels']:
                key = normalise(lab)
                if key:
                    self.key_index[key].add(uri)

        # Drop collisions (key → >1 URI)
        self.collisions: dict[str, list[str]] = {}
        for key, uris in list(self.key_index.items()):
            if len(uris) > 1:
                self.collisions[key] = sorted(uris)
                del self.key_index[key]

    def lookup(self, name: str, location: str | None = None) -> str | None:
        """Return a single matched URI or None.

        Tries qualified key first (name + location), then bare name.
        When NER supplies a location with a known province token, any
        candidate URI whose province token disagrees is rejected — this
        prevents bare-name matches from binding to a same-named place in
        the wrong province (e.g. Fort Simpson BC → Fort Simpson AB).
        """
        ner_provs = all_province_tokens(normalise(f"x, {location}")) if location else set()

        candidates = []
        if name and location:
            candidates.append(normalise(f"{name}, {location}"))
        if name:
            candidates.append(normalise(name))

        for key in candidates:
            uris = self.key_index.get(key)
            if not uris or len(uris) != 1:
                continue
            uri = next(iter(uris))
            if ner_provs:
                uri_prov = self.uri_province.get(uri)
                if uri_prov and uri_prov not in ner_provs:
                    continue
            return uri
        return None

    def stats(self):
        return {
            'total_places': len(self.by_uri),
            'blacklisted_uris': len(self.blacklist),
            'index_keys': len(self.key_index),
            'collision_keys': len(self.collisions),
        }


# ---------------------------------------------------------------------------
# TTL emission
# ---------------------------------------------------------------------------

TTL_HEADER = """@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix crm: <http://www.cidoc-crm.org/cidoc-crm/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix dia: <https://lincsproject.ca/dia/> .

# Section → LINCS place P67_refers_to links, strict NER matching only.
# Generated by scripts/match_places.py — do not edit by hand.

"""


def section_uri(section_id: str) -> str:
    return f"dia:section/{section_id}"


def escape_ttl(s: str) -> str:
    return s.replace('\\', '\\\\').replace('"', '\\"')


def emit_ttl(out_path: Path, section_place_refs: dict, place_meta: dict):
    lines = [TTL_HEADER]

    # Section → place refs
    for section_id, places in sorted(section_place_refs.items()):
        if not places:
            continue
        lines.append(f"<{section_uri(section_id)}> a crm:E73_Information_Object ;")
        refs = ' , '.join(f"<{u}>" for u in sorted(places))
        lines.append(f"    crm:P67_refers_to {refs} .")
        lines.append("")

    # Place stubs
    for uri in sorted({u for refs in section_place_refs.values() for u in refs}):
        meta = place_meta.get(uri, {})
        label = meta.get('canonical', uri)
        lines.append(f"<{uri}> a crm:E53_Place ;")
        lines.append(f'    rdfs:label "{escape_ttl(label)}" ;')
        sameAs = meta.get('sameAs') or set()
        # URI itself is already a GeoNames URI in LINCS; also link any owl:sameAs
        if uri.startswith('https://sws.geonames.org/'):
            lines.append(f"    owl:sameAs <{uri}>")
        elif sameAs:
            sa = next(iter(sameAs))
            lines.append(f"    owl:sameAs <{sa}>")
        lines[-1] = lines[-1] + " ."
        lines.append("")

    out_path.write_text('\n'.join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entities', type=Path,
                    default=Path.home() / 'plato/dia_data/entities_raw')
    ap.add_argument('--lincs', type=Path,
                    default=Path('data/lincs_places_raw.json'))
    ap.add_argument('--ttl', type=Path,
                    default=Path('data/dia-places-1880-1899.ttl'))
    ap.add_argument('--audit', type=Path,
                    default=Path('data/place-matches.json'))
    ap.add_argument('--section-places', type=Path,
                    default=Path('data/section-places.json'),
                    help='Per-section place join (section_id, year, place_uris)')
    ap.add_argument('--years', type=int, nargs=2, default=[1880, 1899])
    args = ap.parse_args()

    idx = LincsPlaceIndex(args.lincs)
    print(f"LINCS place index: {idx.stats()}")

    section_place_refs: dict[str, set[str]] = defaultdict(set)
    section_year: dict[str, int] = {}
    per_year_matched: Counter = Counter()
    per_year_total: Counter = Counter()
    unmatched_counter: Counter = Counter()
    matched_samples: list[dict] = []

    for year in range(args.years[0], args.years[1] + 1):
        fp = args.entities / f"Indian Affairs AR {year}.jsonl"
        if not fp.exists():
            # Handle the "Ar 1900" casing oddity just in case
            alt = args.entities / f"Indian Affairs Ar {year}.jsonl"
            fp = alt if alt.exists() else fp
        if not fp.exists():
            print(f"  skip {year} — no entities file")
            continue

        for line in fp.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            section_id = rec.get('section_id')
            if not section_id:
                continue
            section_year[section_id] = year
            for p in rec.get('places_orgs') or []:
                if p.get('category') != 'PLACE':
                    continue
                name = (p.get('name') or '').strip()
                loc = (p.get('location') or '').strip() or None
                if not name:
                    continue
                per_year_total[year] += 1
                uri = idx.lookup(name, loc)
                if uri:
                    section_place_refs[section_id].add(uri)
                    per_year_matched[year] += 1
                    if len(matched_samples) < 30:
                        matched_samples.append({
                            'year': year,
                            'ner_name': name,
                            'ner_location': loc,
                            'lincs_uri': uri,
                            'lincs_label': idx.by_uri[uri]['canonical'],
                        })
                else:
                    key = f"{name} | {loc or ''}".strip(' |')
                    unmatched_counter[key] += 1

    emit_ttl(args.ttl, section_place_refs, idx.by_uri)

    total_mentions = sum(per_year_total.values())
    total_matched = sum(per_year_matched.values())
    audit = {
        'index_stats': idx.stats(),
        'years': [args.years[0], args.years[1]],
        'total_mentions': total_mentions,
        'total_matched': total_matched,
        'match_rate': round(total_matched / total_mentions, 3) if total_mentions else 0,
        'per_year': {
            str(y): {
                'mentions': per_year_total[y],
                'matched': per_year_matched[y],
                'rate': round(per_year_matched[y] / per_year_total[y], 3)
                if per_year_total[y] else 0,
            }
            for y in sorted(per_year_total)
        },
        'top_unmatched': unmatched_counter.most_common(50),
        'collisions_dropped': len(idx.collisions),
        'collisions_sample': dict(list(idx.collisions.items())[:20]),
        'blacklisted_uris': len(idx.blacklist),
        'blacklist_sample': {
            u: idx.blacklist_reasons[u]
            for u in list(idx.blacklist)[:20]
        },
        'matched_samples': matched_samples,
        'sections_with_places': len(section_place_refs),
    }
    args.audit.write_text(json.dumps(audit, indent=2, default=str))

    # Section-level place join (for downstream aggregation / map payloads)
    section_places_out = [
        {
            'section_id': sid,
            'year': section_year.get(sid),
            'place_uris': sorted(uris),
        }
        for sid, uris in sorted(section_place_refs.items())
    ]
    args.section_places.write_text(json.dumps(section_places_out, indent=2))

    print(f"\n== Place match audit ==")
    print(f"  mentions: {total_mentions}")
    print(f"  matched:  {total_matched} ({audit['match_rate'] * 100:.1f}%)")
    print(f"  sections with ≥1 matched place: {len(section_place_refs)}")
    print(f"  collisions dropped: {len(idx.collisions)}")
    print(f"  URIs blacklisted (conflicting provinces): {len(idx.blacklist)}")
    print(f"\n  TTL:            {args.ttl}")
    print(f"  audit:          {args.audit}")
    print(f"  section-places: {args.section_places}")


if __name__ == '__main__':
    main()
