# Indian Affairs Annual Reports — Linked Open Data Demo

A proof-of-concept showing how Named Entity Recognition + Linked Open Data
grounding unlock historical discovery that keyword search cannot.

**Corpus**: Canada Department of Indian Affairs Annual Reports, 1880–1885
(6 volumes, ~500 sections, ~2,400 person mentions).

**LOD target**: [LINCS Historical Indian Affairs Agents graph](https://lincsproject.ca/docs/explore-lod/project-datasets/ind-affairs/),
CIDOC-CRM modelled, hosted on LINCS Fuseki.

**Demo page**: `index.html` (GitHub Pages)

## What's inside

- `index.html` — the demo narrative, case studies, and embedded visualisations
- `data/dia-mentions-1880-1885.ttl` — CIDOC-CRM Turtle linking our report sections to LINCS agent URIs
- `data/agent-timelines.json` — pre-computed case study payloads (Dewdney, Powell, Boucher)
- `data/regional_tfidf.json` — regional discourse comparison (BC / Prairies / Quebec)
- `queries/` — copy-paste SPARQL queries readers can run against LINCS
- `scripts/` — Python pipeline that produced the data artefacts

## The demo argument

1. **Follow the agent** — three case studies tracing Indian Agents across the
   1880–1885 reports via LINCS-grounded URIs.
2. **Regional discourse at scale** — TF-IDF comparison across BC, Prairies, and
   Quebec corpora, where the *partitions themselves* are only possible because
   agents are grounded to place-aware LOD.
3. **Try it yourself** — live SPARQL against LINCS Fuseki.

The method is corpus-size-invariant: what you see on 500 sections runs
unchanged on 20 million.

## Partners
- [CRKN — Canadian Research Knowledge Network](https://www.crkn-rcdr.ca/)
- [LINCS — Linked Infrastructure for Networked Cultural Scholarship](https://lincsproject.ca/)
