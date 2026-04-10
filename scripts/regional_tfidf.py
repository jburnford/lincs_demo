#!/usr/bin/env python3
"""
Regional TF-IDF comparison of 1880-1885 Indian Affairs report sections.

Method
------
1. For each section, look at the persons extracted by NER and ground them
   to LINCS Indian Affairs Agent URIs (same matching logic as the TTL
   generator).
2. Each grounded agent has one or more places of occupation in LINCS,
   each of which is a GeoNames URI. Map every GeoNames URI to one of three
   regions — British Columbia, Prairies/NWT, Quebec — using a curated rule
   table over GeoNames URI ranges and place labels.
3. Assign each section to the region in which the majority of its grounded
   agents serve. Sections with no grounded agents, or with no majority,
   are dropped (and reported).
4. For each region's section corpus, compute TF-IDF and surface the top
   distinctive terms.

The point of the demo is that this partitioning is impossible without
LOD-grounded agents: keyword search for "British Columbia" would miss
sections that name an agent without naming the province, and false-match
sections where BC is mentioned in passing.

Output
------
data/regional_tfidf.json with:
    {
      "bc":       {"top_terms": [{term, score}, …], "n_sections", "n_agents"},
      "prairies": {…},
      "quebec":   {…},
      "method":   {…},
    }
"""

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
import sys

# Reuse the LINCS index from the TTL generator
sys.path.insert(0, str(Path(__file__).parent))
from generate_ttl import LincsAgentIndex


# ---------------------------------------------------------------------------
# Region assignment from GeoNames place URI / label
# ---------------------------------------------------------------------------

# Curated keyword → region map. Applied to LINCS place labels because
# the GeoNames numeric IDs do not encode province cleanly.
REGION_RULES = {
    'bc': [
        'British Columbia', 'Victoria', 'Vancouver', 'Kamloops', 'Okanagan',
        'Cowichan', 'Nanaimo', 'New Westminster', 'Yale', 'Lytton',
        'Williams Lake', 'Kelowna', 'Kilowna', 'Metlakatla',
    ],
    'prairies': [
        'North-West Territories', 'Northwest Territories', 'Territoires du Nord-Ouest',
        'Manitoba', 'Saskatchewan', 'Alberta', 'Assiniboia', 'Keewatin',
        'Regina', 'Winnipeg', 'Battleford', 'Calgary', 'Edmonton',
        'Prince Albert', 'Qu\'Appelle', 'Fort Macleod', 'Fort Pitt',
        'Indian Head', 'Birtle', 'Birdtail', 'Swan River', 'Touchwood',
        'Carlton', 'Crooked Lake', 'Frog Lake', 'Maple Creek',
        'Blackfoot', 'Blood', 'Piegan', 'Sarcee', 'Stony', 'Morley',
        'File Hills', 'Duck Lake', 'Fort Qu', 'Treaty 4', 'Treaty 6', 'Treaty 7',
        'Shoal Lake',  # NWT-side Shoal Lake (LINCS labels it NWT)
    ],
    'quebec': [
        'Quebec', 'Pierreville', 'Betsiamites', 'Mashteuiatsh', 'Roberval',
        'Caughnawaga', 'Kahnawake', 'Lake of Two Mountains', 'St. Regis',
        'Saint-Régis', 'Saint Regis', 'Restigouche', 'Lake St. John',
        'Lac-Saint-Jean', 'Lorette', 'Maniwaki', 'River Desert', 'Oka',
    ],
}


def region_for_place_label(label: str) -> str | None:
    """Return 'bc' / 'prairies' / 'quebec' / None for a LINCS place label."""
    if not label:
        return None
    lo = label.lower()
    for region, keywords in REGION_RULES.items():
        for kw in keywords:
            if kw.lower() in lo:
                return region
    return None


def regions_for_agent(idx: LincsAgentIndex, uri: str) -> Counter:
    """Tally regions across all of a LINCS agent's known place labels."""
    labels = idx.by_uri[uri]['place_labels']
    tally: Counter = Counter()
    for lbl in labels:
        r = region_for_place_label(lbl)
        if r:
            tally[r] += 1
    return tally


# ---------------------------------------------------------------------------
# Text preprocessing for TF-IDF
# ---------------------------------------------------------------------------

# Very common English stopwords + 19th-century admin filler that doesn't
# differentiate regions and would otherwise dominate the top of every list.
STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because
been before being below between both but by can can't cannot could couldn't did
didn't do does doesn't doing don't down during each few for from further had
hadn't has hasn't have haven't having he he'd he'll he's her here here's hers
herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
it's its itself let's me more most mustn't my myself no nor not of off on once
only or other ought our ours ourselves out over own same shan't she she'd
she'll she's should shouldn't so some such than that that's the their theirs
them themselves then there there's these they they'd they'll they're they've
this those through to too under until up very was wasn't we we'd we'll we're
we've were weren't what what's when when's where where's which while who who's
whom why why's with won't would wouldn't you you'd you'll you're you've your
yours yourself yourselves
also upon thus shall may also one two three four five six seven eight nine ten
year years month months day days last next first second per cent number
report year's herewith honor honour respectfully sir submit submitted made make
made making following thereof therein therefrom hereto herewith said same
many much great good well little large small whole part parts general
agent agents agency agencies department indian indians band bands tribe tribes
people persons men women boys girls
mr mrs dr rev hon esq messrs
""".split())

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


def tokenize(text: str) -> list[str]:
    return [
        w.lower() for w in WORD_RE.findall(text)
        if w.lower() not in STOPWORDS and len(w) > 2
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entities', type=Path,
                    default=Path.home() / 'plato/dia_data/entities_raw')
    ap.add_argument('--sections', type=Path,
                    default=Path.home() / 'plato/dia_data/sections')
    ap.add_argument('--lincs', type=Path,
                    default=Path('data/lincs_agents_raw.json'))
    ap.add_argument('--out', type=Path,
                    default=Path('data/regional_tfidf.json'))
    ap.add_argument('--years', type=int, nargs=2, default=[1880, 1885])
    ap.add_argument('--top-n', type=int, default=20)
    args = ap.parse_args()

    print(f'Loading LINCS agents…')
    idx = LincsAgentIndex(args.lincs)

    # Pre-compute region for each LINCS agent (majority of their place labels)
    agent_region: dict[str, str] = {}
    for uri in idx.by_uri:
        tally = regions_for_agent(idx, uri)
        if tally:
            agent_region[uri] = tally.most_common(1)[0][0]
    print(f'  {len(agent_region)} of {len(idx.by_uri)} LINCS agents assigned to a region')

    # Walk sections, assign each to a region by majority of grounded agents
    region_docs: dict[str, list[list[str]]] = {'bc': [], 'prairies': [], 'quebec': []}
    region_agents: dict[str, set[str]] = {'bc': set(), 'prairies': set(), 'quebec': set()}
    n_total = 0
    n_no_grounded = 0
    n_no_majority = 0
    n_outside = 0  # grounded agents but none in our 3 regions

    for year in range(args.years[0], args.years[1] + 1):
        ent_fp = args.entities / f"Indian Affairs AR {year}.jsonl"
        sec_fp = args.sections / f"Indian Affairs AR {year}.jsonl"
        if not ent_fp.exists() or not sec_fp.exists():
            print(f'  WARN: missing files for {year}')
            continue

        # Build a map section_id → text from sections file
        sec_text: dict[str, str] = {}
        for line in sec_fp.read_text().splitlines():
            if not line.strip():
                continue
            s = json.loads(line)
            sec_text[s['section_id']] = s.get('text', '')

        for line in ent_fp.read_text().splitlines():
            if not line.strip():
                continue
            ent = json.loads(line)
            if ent.get('skipped'):
                continue
            n_total += 1

            # Ground persons in this section
            grounded_uris: list[str] = []
            for p in ent.get('persons') or []:
                nm = p.get('name')
                if not nm:
                    continue
                u = idx.match_person(nm, year)
                if u:
                    grounded_uris.append(u)

            if not grounded_uris:
                n_no_grounded += 1
                continue

            # Tally regions across grounded agents
            region_tally: Counter = Counter()
            for u in grounded_uris:
                r = agent_region.get(u)
                if r:
                    region_tally[r] += 1

            if not region_tally:
                n_outside += 1
                continue

            # Majority region (strict: must be plurality)
            top = region_tally.most_common()
            if len(top) > 1 and top[0][1] == top[1][1]:
                n_no_majority += 1
                continue
            region = top[0][0]

            text = sec_text.get(ent['section_id'], '')
            if not text:
                continue

            tokens = tokenize(text)
            if len(tokens) < 50:
                continue
            region_docs[region].append(tokens)
            for u in grounded_uris:
                if agent_region.get(u) == region:
                    region_agents[region].add(u)

    print(f'\nSection assignment:')
    print(f'  Total sections:           {n_total}')
    print(f'  Dropped — no grounded:    {n_no_grounded}')
    print(f'  Dropped — outside 3 regs: {n_outside}')
    print(f'  Dropped — no majority:    {n_no_majority}')
    for r in ('bc', 'prairies', 'quebec'):
        print(f'  → {r:8s}: {len(region_docs[r])} sections, {len(region_agents[r])} agents')

    # ---- TF-IDF across the three corpora (each region = 1 "document")
    # Per-region term frequencies
    region_tf: dict[str, Counter] = {}
    for r, docs in region_docs.items():
        c: Counter = Counter()
        for d in docs:
            c.update(d)
        region_tf[r] = c

    # Document frequency = how many regions a term appears in (1, 2, or 3)
    df: Counter = Counter()
    for r, c in region_tf.items():
        for term in c:
            df[term] += 1

    N = 3  # three regions
    # TF-IDF = (tf in region / total tokens in region) * log(N / df)
    output: dict = {}
    for r, c in region_tf.items():
        total = sum(c.values())
        if total == 0:
            continue
        scored: list[tuple[str, float]] = []
        for term, tf in c.items():
            # Minimum frequency floor — drop terms appearing fewer than 5 times
            if tf < 5:
                continue
            idf = math.log(N / df[term]) if df[term] > 0 else 0
            if idf == 0:
                continue  # appears in all 3 regions, no distinguishing power
            score = (tf / total) * idf
            scored.append((term, score))
        scored.sort(key=lambda x: -x[1])
        top = scored[:args.top_n]
        output[r] = {
            'top_terms': [{'term': t, 'score': round(s * 1000, 4)} for t, s in top],
            'n_sections': len(region_docs[r]),
            'n_agents': len(region_agents[r]),
            'n_tokens': total,
        }

    output['method'] = {
        'total_sections_considered': n_total,
        'dropped_no_grounded_agents': n_no_grounded,
        'dropped_outside_three_regions': n_outside,
        'dropped_no_majority_region': n_no_majority,
        'min_term_frequency': 5,
        'note': (
            'TF-IDF computed across three region-level "documents". '
            'Each section was assigned to a region by majority vote of '
            'its grounded LINCS Indian Affairs Agents, never by string '
            'matching on the section text.'
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2))
    print(f'\nWrote {args.out}')

    # Sneak peek
    print('\nTop 8 distinctive terms per region:')
    for r in ('bc', 'prairies', 'quebec'):
        if r in output:
            terms = ', '.join(t['term'] for t in output[r]['top_terms'][:8])
            print(f'  {r:8s}: {terms}')


if __name__ == '__main__':
    main()
