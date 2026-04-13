#!/usr/bin/env bash
# Fetch the LINCS Indian Affairs Agents graph subset we need for the demo.
# The resulting JSON (data/lincs_agents_raw.json) is ~22MB and gitignored;
# run this script once to populate it before running the Python pipeline.

set -euo pipefail
cd "$(dirname "$0")/.."

OUT=data/lincs_agents_raw.json
mkdir -p data

read -r -d '' QUERY <<'SPARQL' || true
PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX event: <http://id.lincsproject.ca/event/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT DISTINCT ?agent ?agentName ?occLabel ?place ?placeLabel ?agency ?agencyLabel ?begin ?end
WHERE {
  GRAPH <http://graph.lincsproject.ca/hist-canada/ind-affairs> {
    ?occ a crm:E7_Activity ;
         crm:P14_carried_out_by ?agent ;
         crm:P2_has_type event:OccupationEvent ;
         rdfs:label ?occLabel ;
         crm:P4_has_time-span ?ts .
    ?agent rdfs:label ?agentName .
    ?ts crm:P82a_begin_of_the_begin ?begin .
    OPTIONAL { ?ts crm:P82b_end_of_the_end ?end . }
    OPTIONAL { ?occ crm:P7_took_place_at ?place . ?place rdfs:label ?placeLabel . }
    OPTIONAL { ?occ crm:P11_had_participant ?agency . ?agency a crm:E74_Group ; rdfs:label ?agencyLabel . }
    FILTER(?begin <= "1899-12-31T23:59:59"^^xsd:dateTime
           && (!BOUND(?end) || ?end >= "1880-01-01T00:00:00"^^xsd:dateTime))
  }
}
SPARQL

echo "Fetching LINCS agents for 1880-1899…"
curl -s -X POST https://fuseki.lincsproject.ca/lincs/sparql \
  -H "Content-Type: application/sparql-query" \
  -H "Accept: application/sparql-results+json" \
  --data-binary "$QUERY" > "$OUT"

rows=$(python3 -c "import json; print(len(json.load(open('$OUT'))['results']['bindings']))")
echo "Wrote $OUT ($rows rows)"
