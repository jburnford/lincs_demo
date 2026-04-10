#!/usr/bin/env python3
"""
Generate CIDOC-CRM Turtle linking 1880-1885 Indian Affairs report sections
to grounded LINCS / GeoNames URIs.

Each section becomes an E73_Information_Object with:
  - rdfs:label
  - P67_refers_to → grounded person URIs
  - P67_refers_to → grounded place URIs
  - P4_has_time-span → year
  - P190_has_symbolic_content → section_id (as identifier text)

Each grounded person mention generates a stub E21_Person with owl:sameAs.
Each grounded place generates a stub E53_Place with owl:sameAs to GeoNames.

Person matching: surname + first initial + year-of-occupation overlap
against the LINCS Historical Indian Affairs Agents graph.

Place matching: case-insensitive substring on the LINCS-known place labels.

Usage:
    python3 scripts/generate_ttl.py \
        --entities ~/plato/dia_data/entities_raw \
        --lincs data/lincs_agents_raw.json \
        --out data/dia-mentions-1880-1885.ttl
"""

import argparse
import json
import re
from collections import defaultdict
from hashlib import sha1
from pathlib import Path


# ---------------------------------------------------------------------------
# Name normalisation (shared with grounding logic)
# ---------------------------------------------------------------------------

TITLE_RE = re.compile(
    r'\b(Dr|Mr|Mrs|Ms|Rev|Hon|Sir|M\.?D|Esq|Capt|Col|Lt|Lieut|Major|Gen|Prof)\.?\b',
    re.I,
)


def strip_titles(name: str) -> str:
    return TITLE_RE.sub('', name)


def surname_initial(name: str):
    """Heuristic (surname_lower, first_initial_lower or None) tuple.

    Surname = longest token after stripping titles and punctuation.
    First initial = first letter of any non-surname token.
    """
    n = strip_titles(name)
    n = re.sub(r'[^\w\s]', ' ', n)
    toks = [t for t in n.split() if t]
    if not toks:
        return None
    surname = max(toks, key=len).lower()
    inits = [t[0].lower() for t in toks if t.lower() != surname]
    return (surname, inits[0] if inits else None)


def slugify(text: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return s[:50] or sha1(text.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# LINCS index
# ---------------------------------------------------------------------------

class LincsAgentIndex:
    """Look up LINCS agents by surname+initial, with year-overlap filtering."""

    def __init__(self, lincs_json_path: Path):
        data = json.loads(lincs_json_path.read_text())
        self.by_uri: dict[str, dict] = {}
        self.by_si: dict[tuple, list[str]] = defaultdict(list)
        self.places: dict[str, str] = {}  # normalised label → URI

        for r in data['results']['bindings']:
            uri = r['agent']['value']
            entry = self.by_uri.setdefault(
                uri,
                {
                    'names': set(),
                    'place_uris': set(),
                    'place_labels': set(),
                    'agencies': set(),
                    'years': set(),
                },
            )
            entry['names'].add(r['agentName']['value'])
            if 'place' in r:
                entry['place_uris'].add(r['place']['value'])
                entry['place_labels'].add(r['placeLabel']['value'])
                self.places[r['placeLabel']['value'].lower()] = r['place']['value']
            if 'agency' in r:
                entry['agencies'].add(r['agencyLabel']['value'])
            entry['years'].add(int(r['begin']['value'][:4]))

        # Build surname+initial lookup
        for uri, info in self.by_uri.items():
            for nm in info['names']:
                si = surname_initial(nm)
                if si:
                    self.by_si[si].append(uri)

    def match_person(self, name: str, year: int) -> str | None:
        """Return best LINCS agent URI for (name, year), or None."""
        si = surname_initial(name)
        if not si:
            return None
        candidates = list(self.by_si.get(si, []))
        if not candidates and si[1] is not None:
            # surname-only fallback
            candidates = list(self.by_si.get((si[0], None), []))
        # Filter by year overlap (LINCS occupation year ≤ report year)
        viable = [
            uri for uri in candidates
            if any(y <= year for y in self.by_uri[uri]['years'])
        ]
        if not viable:
            return None
        # Prefer the agent whose earliest year is closest below the report year
        viable.sort(
            key=lambda u: abs(year - max(y for y in self.by_uri[u]['years'] if y <= year))
        )
        return viable[0]

    def match_place(self, place_name: str) -> tuple[str, str] | None:
        """Return (uri, canonical_label) for a LINCS place, or None.

        Tries exact label, then 'name, province'-style trimming, then
        substring match on the LINCS-known place labels.
        """
        if not place_name:
            return None
        key = place_name.lower().strip()
        # Exact
        if key in self.places:
            return (self.places[key], place_name)
        # Strip province suffix
        head = key.split(',')[0].strip()
        for label_lc, uri in self.places.items():
            if label_lc.split(',')[0].strip() == head:
                return (uri, place_name)
        # Substring fallback (head must be ≥4 chars to avoid noise)
        if len(head) >= 4:
            for label_lc, uri in self.places.items():
                if head in label_lc or label_lc.split(',')[0].strip() in head:
                    return (uri, place_name)
        return None


# ---------------------------------------------------------------------------
# Turtle emission
# ---------------------------------------------------------------------------

PREFIXES = """\
@prefix crm:    <http://www.cidoc-crm.org/cidoc-crm/> .
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .
@prefix owl:    <http://www.w3.org/2002/07/owl#> .
@prefix biography: <http://id.lincsproject.ca/biography/> .
@prefix dia:    <https://jburnford.github.io/lincs_demo/id/> .

"""


def ttl_literal(s: str) -> str:
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def emit_section(section, person_uris, place_uris):
    """Emit Turtle for a single section as E73_Information_Object."""
    sec_id = section['section_id']
    year = section['year']
    label = f"DIA Annual Report {year} — {section.get('agency') or sec_id}"
    sec_uri = f"dia:section/{sec_id}"
    name_uri = f"dia:name/section-{sec_id}"
    ts_uri = f"dia:timespan/{year}"

    out = []
    out.append(f"{sec_uri} a crm:E73_Information_Object ;")
    out.append(f"    rdfs:label {ttl_literal(label)}@en ;")
    out.append(f"    crm:P1_is_identified_by {name_uri} ;")
    out.append(f"    crm:P4_has_time-span {ts_uri}")
    refs = sorted(set(person_uris) | set(place_uris))
    if refs:
        out[-1] += " ;"
        ref_lines = [f"        <{u}>" for u in refs]
        out.append(f"    crm:P67_refers_to\n" + " ,\n".join(ref_lines))
    out[-1] += " .\n"

    # Name appellation for the section
    out.append(f"{name_uri} a crm:E33_E41_Linguistic_Appellation ;")
    out.append(f"    rdfs:label {ttl_literal('Title of ' + label)}@en ;")
    out.append(f"    crm:P2_has_type biography:groupName ;")
    out.append(f"    crm:P190_has_symbolic_content {ttl_literal(label)}^^xsd:string .\n")

    # Time-span
    out.append(f"{ts_uri} a crm:E52_Time-Span ;")
    out.append(f"    rdfs:label {ttl_literal(str(year))}@en ;")
    out.append(f"    crm:P82_at_some_time_within {ttl_literal(str(year))}^^xsd:string ;")
    out.append(f"    crm:P82a_begin_of_the_begin \"{year}-01-01T00:00:00\"^^xsd:dateTime ;")
    out.append(f"    crm:P82b_end_of_the_end \"{year}-12-31T23:59:59\"^^xsd:dateTime .\n")

    return "\n".join(out)


def emit_person_stub(uri: str, label: str) -> str:
    name_uri = f"dia:name/person-{slugify(label)}"
    return (
        f"<{uri}> a crm:E21_Person ;\n"
        f"    rdfs:label {ttl_literal(label)}@en ;\n"
        f"    crm:P1_is_identified_by {name_uri} .\n\n"
        f"{name_uri} a crm:E33_E41_Linguistic_Appellation ;\n"
        f"    rdfs:label {ttl_literal('Name of ' + label)}@en ;\n"
        f"    crm:P2_has_type biography:personalName ;\n"
        f"    crm:P190_has_symbolic_content {ttl_literal(label)}^^xsd:string .\n"
    )


def emit_place_stub(uri: str, label: str) -> str:
    return (
        f"<{uri}> a crm:E53_Place ;\n"
        f"    rdfs:label {ttl_literal(label)}@en .\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entities', type=Path, required=True,
                    help='Directory containing entities_raw/*.jsonl')
    ap.add_argument('--lincs', type=Path, required=True,
                    help='LINCS agents JSON dump (SPARQL results)')
    ap.add_argument('--out', type=Path, required=True,
                    help='Output Turtle file')
    ap.add_argument('--years', type=int, nargs=2, default=[1880, 1885])
    args = ap.parse_args()

    print(f'Loading LINCS agents from {args.lincs}…')
    idx = LincsAgentIndex(args.lincs)
    print(f'  {len(idx.by_uri)} agents, {len(idx.places)} places known')

    grounded_persons: dict[str, str] = {}  # uri → display label
    grounded_places: dict[str, str] = {}   # uri → display label
    sections_written = 0

    body_chunks: list[str] = [PREFIXES]
    body_chunks.append(f"# Generated by scripts/generate_ttl.py\n")
    body_chunks.append(
        f"# Source: Indian Affairs Annual Reports {args.years[0]}–{args.years[1]}\n"
    )
    body_chunks.append("# Grounding: LINCS Historical Indian Affairs Agents graph\n\n")

    n_persons_seen = 0
    n_persons_grounded = 0
    n_places_seen = 0
    n_places_grounded = 0

    for year in range(args.years[0], args.years[1] + 1):
        fp = args.entities / f"Indian Affairs AR {year}.jsonl"
        if not fp.exists():
            print(f'  WARN: {fp} missing, skipping')
            continue
        for line in fp.read_text().splitlines():
            if not line.strip():
                continue
            section = json.loads(line)
            if section.get('skipped'):
                continue

            person_uris: list[str] = []
            for p in section.get('persons') or []:
                nm = p.get('name')
                if not nm:
                    continue
                n_persons_seen += 1
                uri = idx.match_person(nm, year)
                if uri:
                    n_persons_grounded += 1
                    person_uris.append(uri)
                    if uri not in grounded_persons:
                        # Prefer the LINCS canonical name
                        grounded_persons[uri] = sorted(idx.by_uri[uri]['names'])[0]

            place_uris: list[str] = []
            for pl in section.get('places_orgs') or []:
                if pl.get('category') and pl['category'] != 'PLACE':
                    # Skip orgs for now — handled via agency relations later
                    pass
                nm = pl.get('name')
                if not nm:
                    continue
                n_places_seen += 1
                hit = idx.match_place(nm)
                if hit:
                    uri, lbl = hit
                    n_places_grounded += 1
                    place_uris.append(uri)
                    if uri not in grounded_places:
                        grounded_places[uri] = lbl

            body_chunks.append(emit_section(section, person_uris, place_uris))
            body_chunks.append('\n')
            sections_written += 1

    # Append entity stubs
    body_chunks.append('\n# --- Grounded Persons ---\n\n')
    for uri, label in sorted(grounded_persons.items()):
        body_chunks.append(emit_person_stub(uri, label))
        body_chunks.append('\n')

    body_chunks.append('\n# --- Grounded Places (GeoNames) ---\n\n')
    for uri, label in sorted(grounded_places.items()):
        body_chunks.append(emit_place_stub(uri, label))
        body_chunks.append('\n')

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(''.join(body_chunks))

    print()
    print(f'Sections written:    {sections_written}')
    print(f'Person mentions:     {n_persons_seen} seen, {n_persons_grounded} grounded ({100*n_persons_grounded/max(n_persons_seen,1):.1f}%)')
    print(f'Distinct LINCS persons: {len(grounded_persons)}')
    print(f'Place mentions:      {n_places_seen} seen, {n_places_grounded} grounded ({100*n_places_grounded/max(n_places_seen,1):.1f}%)')
    print(f'Distinct LINCS places:  {len(grounded_places)}')
    print(f'Output:              {args.out}')


if __name__ == '__main__':
    main()
