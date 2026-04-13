# Indian Affairs Annual Reports — Linked Open Data Demo

A proof-of-concept showing how Named Entity Recognition + Linked Open Data
grounding unlock historical discovery that keyword search cannot.

**Corpus**: Canada Department of Indian Affairs Annual Reports, 1880–1899
(20 volumes, 2,123 sections, 13,146 person mentions, 18,199 place mentions).

**LOD target**: [LINCS Historical Indian Affairs Agents graph](https://lincsproject.ca/docs/explore-lod/project-datasets/ind-affairs/),
CIDOC-CRM modelled, hosted on LINCS Fuseki.

**Demo page**: `index.html` (GitHub Pages)

## What's inside

- `index.html` — the demo narrative, case studies, and embedded visualisations
- `data/dia-mentions-1880-1899.ttl` — agent-grounded TTL (3,443 person mentions → 524 LINCS agent URIs)
- `data/dia-places-1880-1899.ttl` — place-grounded TTL (2,965 place mentions → LINCS/GeoNames place URIs, strict matching)
- `data/place-matches.json` — audit output for the place matcher (match rate, blacklist, collisions, top unmatched)
- `data/agent-timelines.json` — pre-computed case study payloads (Dewdney, Powell, Boucher)
- `data/regional_tfidf.json` — regional discourse comparison (BC / Prairies / Quebec)
- `queries/` — copy-paste SPARQL queries readers can run against LINCS
- `scripts/` — Python pipeline that produced the data artefacts

## The demo argument

1. **Follow the agent** — three case studies tracing Indian Agents across the
   1880–1899 reports via LINCS-grounded URIs.
2. **Regional discourse at scale** — TF-IDF comparison across BC, Prairies, and
   Quebec corpora, where the *partitions themselves* are only possible because
   agents are grounded to place-aware LOD.
3. **Try it yourself** — live SPARQL against LINCS Fuseki.

Place grounding is deliberately strict: `scripts/match_places.py` only
emits a link when a normalised NER mention resolves to exactly one LINCS
place URI, after blacklisting URIs whose labels span multiple provinces
and dropping normalised keys that collide across URIs. The 16% match
rate is a floor on precision — the unmatched 84% is the gap between
"places LINCS Indian Affairs knows" and "places the reports talk about",
not pipeline noise.

The method is corpus-size-invariant: what you see on 2,123 sections runs
unchanged on 20 million.

## Partners
- [CRKN — Canadian Research Knowledge Network](https://www.crkn-rcdr.ca/)
- [LINCS — Linked Infrastructure for Networked Cultural Scholarship](https://lincsproject.ca/)
