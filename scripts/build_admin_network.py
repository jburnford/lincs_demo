#!/usr/bin/env python3
"""
Build the administrative-network payload from bc-agents-kg.json.

Extracts person→person "administrative" edges (reports_to, employed_by,
superintends), consolidates name variants (e.g. "P. O'REILLY" and
"P. O'Reilly, Esq." → one node), resolves to LINCS agent URIs where the
normalised name matches a BC-serving agent, and emits a small JSON file
suitable for a force-directed graph.

Output: data/bc-admin-network.json
  {
    "nodes": [
      { "id", "label", "grounded_uri"?, "in_degree", "out_degree",
        "years": [min, max], "section_count" }
    ],
    "edges": [
      { "source", "target", "predicate", "years": [...], "count",
        "evidence_sample": "..." }
    ],
    "stats": {...}
  }
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_ttl import strip_titles  # noqa: E402


# Predicates that describe person→person administrative relationships
ADMIN_PREDICATES = {
    'reports_to',
    'employed_by',
    'superintends',
}

# Manual audit blocklist — edges that are predicate-wrong or direction-wrong
# after human review. See notes in the admin-network commit / audit.
AUDIT_BLOCKLIST = {
    # (source_key, target_key, predicate) → reason
    ('meason', 'johns', 'reports_to'):
        "succession ('relieved by Mr. Gomer Johns, to whom I handed over') — not a reporting relationship",
    ('mackay', 'tunstall', 'reports_to'):
        "letter correspondence — direction ambiguous in evidence",
}

# Heuristic: a string "looks like a person" if it contains capitalized
# tokens and no obvious place / org markers.
ORG_MARKERS = re.compile(
    r'\b(agency|superintendency|bands?|tribes?|reserves?|schools?|departments?|'
    r'offices?|governments?|commissions?|compan(?:y|ies)|councils?|parliament|'
    r'crown|missions?|society|college|province|territory|penitentiary|islands?|'
    r'rivers?|lakes?|bays?|mountains?|forts?|ports?|cit(?:y|ies)|towns?|canals?|'
    r'railways?|railroads?|sawmills?|mills?|mines?|churches?|stores?|courts?|'
    r'treat(?:y|ies)|nations?|hospitals?|asylums?|inlets?|indians|chiefs?|'
    r'people|community|camps?|valleys?|creeks?|harbours?|harbors?|firm|ltd|'
    r'incorporated|limited|\& )\b',
    re.I,
)

# Generic role titles that aren't named persons (allow trailing qualifiers)
GENERIC_ROLES = re.compile(
    r'^\s*(the\s+)?(superintendent(?:[\s-]general)?|indian\s+agent|'
    r'commissioner|deputy|chief|inspector|magistrate|governor|'
    r'minister|premier)(\s+(of|for|at|in)\s+.*)?$',
    re.I,
)


def looks_like_person(s: str) -> bool:
    if not s:
        return False
    if '&' in s:  # firm/partnership
        return False
    if ORG_MARKERS.search(s):
        return False
    if GENERIC_ROLES.match(s):
        return False
    toks = s.split()
    if not toks:
        return False
    cap = sum(1 for t in toks if t[:1].isupper() or t.isupper())
    return cap >= 1 and len(toks) <= 6


def node_key(name: str) -> str:
    """Consolidate name variants to a single graph node ID.

    Strategy: extract the surname. Handles "Surname, Given" (LINCS
    format) and "Given Surname" (NER format). The key is the lowercase
    surname — rare collisions between unrelated same-surname people
    are tolerated in exchange for cleanly merging name variants like
    "Dr. Powell", "I.W. Powell", and "POWELL".
    """
    if not name:
        return ''
    s = strip_titles(name)
    # Drop common post-nominal suffixes
    s = re.sub(r'\b(esq|jr|sr|md|m\.d|ma|ba|ll\.?d|dd|phd)\.?\b', '', s, flags=re.I)
    # Strip punctuation except commas (which indicate "Surname, Given")
    s = re.sub(r"[^\w\s,']", ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if not s:
        return ''
    # LINCS-format "Surname, Given" — exactly one token before comma
    surname = None
    if ',' in s:
        before, _, _after = s.partition(',')
        before_toks = before.split()
        if len(before_toks) == 1 and len(before_toks[0]) >= 2:
            surname = before_toks[0]
    # Otherwise: last alpha token of length ≥3 in the whole string
    if not surname:
        toks = [t for t in s.split() if len(t) >= 3 and t[0].isalpha()]
        if not toks:
            toks = s.split()
        surname = toks[-1] if toks else s
    # Normalise apostrophes (O'Reilly → oreilly)
    return re.sub(r"[^a-z]", '', surname.lower())


def pretty_label(variants: list[str]) -> str:
    """Pick the most readable label from seen name variants.

    Prefer mixed-case over all-caps; prefer longer over shorter; prefer
    forms with punctuation (initials) over bare.
    """
    def score(v: str) -> tuple:
        mixed = any(c.islower() for c in v)
        has_punct = bool(re.search(r'[.,]', v))
        return (mixed, has_punct, len(v))
    return max(variants, key=score)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kg', type=Path, default=Path('data/bc-agents-kg.json'))
    ap.add_argument('--out', type=Path,
                    default=Path('data/bc-admin-network.json'))
    args = ap.parse_args()

    kg = json.loads(args.kg.read_text())

    # BC agent name lookup: normalised key → URI + canonical label
    agent_by_key: dict[str, dict] = {}
    for a in kg['agents']:
        for nm in a['names']:
            k = node_key(nm)
            if k and k not in agent_by_key:
                agent_by_key[k] = {'uri': a['uri'], 'canonical': a['canonical_name']}

    # Collect edges
    nodes: dict[str, dict] = {}
    edges_agg: dict[tuple, dict] = {}

    def get_node(raw: str):
        k = node_key(raw)
        if not k:
            return None
        n = nodes.setdefault(k, {
            'id': k,
            'label_variants': [],
            'grounded_uri': None,
            'in_degree': 0,
            'out_degree': 0,
            'years': set(),
            'section_ids': set(),
        })
        if raw not in n['label_variants']:
            n['label_variants'].append(raw)
        if k in agent_by_key:
            n['grounded_uri'] = agent_by_key[k]['uri']
        return n

    skipped = Counter()
    audit_filtered = []
    for r in kg['relationships']:
        pred = r['predicate'].lower()
        if pred not in ADMIN_PREDICATES:
            continue
        subj = r['subject']
        obj = r['object']
        if not looks_like_person(subj):
            skipped['subject_not_person'] += 1
            continue
        if not looks_like_person(obj):
            skipped['object_not_person'] += 1
            continue
        s_node = get_node(subj)
        o_node = get_node(obj)
        if not s_node or not o_node or s_node['id'] == o_node['id']:
            continue
        # Manual audit filter
        audit_key = (s_node['id'], o_node['id'], pred)
        if audit_key in AUDIT_BLOCKLIST:
            audit_filtered.append({
                'source': subj, 'target': obj, 'predicate': pred,
                'year': r['year'], 'reason': AUDIT_BLOCKLIST[audit_key],
            })
            skipped['audit_filtered'] += 1
            continue

        year = r['year']
        sid = r['section_id']
        s_node['years'].add(year)
        s_node['section_ids'].add(sid)
        s_node['out_degree'] += 1
        o_node['years'].add(year)
        o_node['section_ids'].add(sid)
        o_node['in_degree'] += 1

        ekey = (s_node['id'], o_node['id'], pred)
        e = edges_agg.setdefault(ekey, {
            'source': s_node['id'],
            'target': o_node['id'],
            'predicate': pred,
            'years': set(),
            'count': 0,
            'evidence_sample': None,
            'section_ids': set(),
        })
        e['years'].add(year)
        e['count'] += 1
        e['section_ids'].add(sid)
        if not e['evidence_sample'] and r.get('evidence'):
            e['evidence_sample'] = r['evidence'][:240]

    # Synthesise a central "Department of Indian Affairs" hub so the
    # graph is a single connected component. Every person node gets an
    # `employed_by` edge to the department.
    DIA_ID = '__dia__'
    dia_node_ref = {
        'id': DIA_ID,
        'label_variants': ['Department of Indian Affairs'],
        'grounded_uri': None,
        'in_degree': 0,
        'out_degree': 0,
        'years': set(),
        'section_ids': set(),
        'is_department': True,
    }
    nodes[DIA_ID] = dia_node_ref
    for nid, n in list(nodes.items()):
        if nid == DIA_ID:
            continue
        ekey = (nid, DIA_ID, 'employed_by')
        edges_agg.setdefault(ekey, {
            'source': nid,
            'target': DIA_ID,
            'predicate': 'employed_by',
            'years': set(n['years']),
            'count': 0,
            'evidence_sample': None,
            'section_ids': set(n['section_ids']),
            'synthetic': True,
        })
        dia_node_ref['in_degree'] += 1
        dia_node_ref['years'] |= n['years']
        dia_node_ref['section_ids'] |= n['section_ids']
        n['out_degree'] += 1  # connection to department

    # Finalize nodes
    nodes_out = []
    for n in nodes.values():
        years = sorted(n['years'])
        nodes_out.append({
            'id': n['id'],
            'label': pretty_label(n['label_variants']),
            'grounded_uri': n['grounded_uri'],
            'in_degree': n['in_degree'],
            'out_degree': n['out_degree'],
            'years': [years[0], years[-1]] if years else None,
            'section_count': len(n['section_ids']),
            'is_department': n.get('is_department', False),
        })
    nodes_out.sort(key=lambda n: -(n['in_degree'] + n['out_degree']))

    edges_out = []
    for e in edges_agg.values():
        years = sorted(e['years'])
        edges_out.append({
            'source': e['source'],
            'target': e['target'],
            'predicate': e['predicate'],
            'years': [years[0], years[-1]] if years else None,
            'count': e['count'],
            'section_count': len(e['section_ids']),
            'evidence_sample': e['evidence_sample'],
            'synthetic': e.get('synthetic', False),
        })
    edges_out.sort(key=lambda e: -e['count'])

    stats = {
        'nodes': len(nodes_out),
        'edges': len(edges_out),
        'grounded_nodes': sum(1 for n in nodes_out if n['grounded_uri']),
        'by_predicate': dict(Counter(e['predicate'] for e in edges_out)),
        'skipped': dict(skipped),
        'audit_filtered': audit_filtered,
    }

    payload = {'nodes': nodes_out, 'edges': edges_out, 'stats': stats}
    args.out.write_text(json.dumps(payload, indent=2))

    print(f'Nodes: {stats["nodes"]}  ({stats["grounded_nodes"]} grounded to LINCS)')
    print(f'Edges: {stats["edges"]}  {stats["by_predicate"]}')
    print(f'Skipped: {stats["skipped"]}')
    print()
    print('Top hubs (in-degree):')
    for n in sorted(nodes_out, key=lambda n: -n['in_degree'])[:10]:
        grounded = ' *' if n['grounded_uri'] else ''
        print(f'  in={n["in_degree"]:>2}  out={n["out_degree"]:>2}  {n["label"]}{grounded}')
    print(f'\nWrote {args.out}')


if __name__ == '__main__':
    main()
