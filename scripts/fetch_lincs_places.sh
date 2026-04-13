#!/usr/bin/env bash
# Fetch the full LINCS Indian Affairs place inventory for strict NER matching.
# Every place referenced by any occupation event in the Ind. Affairs graph.
# Output includes label, optional WKT (P168), and the GeoNames sameAs URI.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=data/lincs_places_raw.json
mkdir -p data

read -r -d '' QUERY <<'SPARQL' || true
PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>

SELECT DISTINCT ?place ?label ?wkt ?sameAs WHERE {
  GRAPH <http://graph.lincsproject.ca/hist-canada/ind-affairs> {
    ?occ a crm:E7_Activity ;
         crm:P7_took_place_at ?place .
    ?place rdfs:label ?label .
    OPTIONAL { ?place crm:P168_place_is_defined_by ?wkt . }
    OPTIONAL { ?place owl:sameAs ?sameAs . }
  }
}
SPARQL

echo "Fetching LINCS Indian Affairs place inventory…"
curl -s -X POST https://fuseki.lincsproject.ca/lincs/sparql \
  -H "Content-Type: application/sparql-query" \
  -H "Accept: application/sparql-results+json" \
  --data-binary "$QUERY" > "$OUT"

rows=$(python3 -c "import json; print(len(json.load(open('$OUT'))['results']['bindings']))")
echo "Wrote $OUT ($rows rows)"
