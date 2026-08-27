#!/bin/bash
# The DELIBERATE content set for the local rig. Run against a freshly built Ontoserver.
#
#   ./load-content.sh http://localhost:8080/fhir
#
# DESIGN -- packages vs individual resources.
# IG packages are loaded whole, because the corpus validates against their profiles and we
# want several versions present: a real server holds more than one, and unpinned resolution
# across them is part of what this rig measures (see ontoserver4 #1543-#1547).
#
# hl7.terminology.r4 is NOT loaded whole. The AU IGs pin exactly SIX versioned THO
# canonicals; pulling five full packages to satisfy them adds ~20k unused resources AND
# extra versions of everything else, which changes the resolution behaviour under test.
# So those come in as individual CodeSystems, extracted at the pinned version.
#
# fixtures/ CodeSystems are the empirical set: run the sweep, and for any unknown-system
# error on a system that SHOULD resolve, add its CodeSystem here. Identifier systems, UCUM
# and example/fictional systems deliberately get no fixture.
# codesystem-{anzsic,atc,cvx,pbs}.json come from the ig-validation-sweep skill on branch
# experiment/validate-new-engine-full-suite.
set -euo pipefail
NCTS_BUNDLE_VERSION=20260831
SCT_VERSION=http://snomed.info/sct/32506021000036107/version/20260831
LOINC_VERSION=2.82
BASE="${1:?usage: ./load-content.sh <fhir-base>}"
cd "$(dirname "$0")"

echo "== IG packages (several versions, deliberately) =="
# DESIGN: this list is the DEPENDENCY CLOSURE of the AU IGs, read from each package's
# package.json -- not a hand-picked set. Both extensions versions are genuinely required
# (5.2.0 by au.core 2.0.0 / au.base 6.0.0 / au.ereq / ips 2.0.0; 5.3.0 by au.core
# 3.0.0-ballot1 / au.base 7.0.0-ballot1 / au.ps / ips 2.0.1), which is also where the
# deliberate multi-version condition comes from.
#
# hl7.fhir.r4.core IS listed, after initially being left out. Ontoserver
# resolves base R4 internally for its OWN validation -- but it must also ACT AS THE TX
# SERVER for the wrapper arm, and over REST it served none of the core value sets:
# $expand of encounter-status / medication-statement-status / mimetypes all failed with
# "Could not find value set". The wrapper then reported valid codes ('finished',
# 'active', 'application/pdf') as ERRORS, inflating the "Ontoserver is silent" bucket
# with ~20 files of pure rig artefact. The real tx.dev.hl7.org.au serves all of them.
# So r4.core IS required here -- not for Ontoserver, for its tx client.
./load-igs.py "$BASE" \
  hl7.fhir.r4.core#4.0.1 \
  hl7.fhir.uv.extensions.r4#5.2.0 \
  hl7.fhir.uv.extensions.r4#5.3.0 \
  hl7.fhir.uv.smart-app-launch#2.2.0 \
  hl7.fhir.uv.ipa#1.1.0 \
  hl7.fhir.au.base#6.0.0 \
  hl7.fhir.au.base#7.0.0-ballot1 \
  hl7.fhir.au.core#2.0.0 \
  hl7.fhir.au.core#3.0.0-ballot1 \
  hl7.fhir.au.ps#1.0.0 \
  hl7.fhir.au.ereq#1.0.0 \
  hl7.fhir.uv.ips#2.0.0 \
  hl7.fhir.uv.ips#2.0.1 \
  ihe.formatcode.fhir#1.1.0

echo
echo "== cross-version extensions (CHERRY-PICKED, not the whole package) =="
# hl7.fhir.uv.xver-r5.r4 is a declared dependency of au.base 7.0.0-ballot1, but the
# package is 4708 resources of which 645 are CodeSystems. Loading it whole took this
# server from 93 CodeSystems to 788 -- re-inflating exactly the terminology surface the
# lean-THO decision strips out. The AU IGs reference 18 StructureDefinitions from it and
# none of its terminology, so only those 18 are loaded. See load-xver.py.
./load-xver.py "$BASE"

echo
echo "== individual CodeSystems (NOT whole THO) =="
for f in fixtures/tho/*.json fixtures/codesystem-*.json; do
  [ -f "$f" ] || continue
  id=$(python3 -c "import json,sys;print(json.load(open('$f'))['id'])")
  code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$BASE/CodeSystem/$id" \
        -H 'Content-Type: application/fhir+json' --data-binary @"$f")
  printf '  %-46s -> HTTP %s\n' "$(basename "$f")" "$code"
done

echo
echo "== NCTS FHIR resource bundle (R4) =="
# The Australian national terminology set, via Ontoserver's /api/addBundle. Loaded WHOLE,
# unlike THO and xver: this is AU content that a real HL7 AU Ontoserver holds, not an
# upstream dependency package we are trimming. Supplies the AIR vaccine CodeSystem the
# corpus uses. Needs NCTS_CLIENT_ID / NCTS_CLIENT_SECRET in the environment.
./load-ncts.py "$BASE" "$NCTS_BUNDLE_VERSION"

echo
echo "== NCTS binary indexes (SNOMED CT-AU, LOINC) =="
# DESIGN: the syndication client installs these itself once the feed has synced, so an
# explicit indexCodeSystem call races it and 500s on a cold server. Poll for the desired
# version to reach indexStatus=OK and HARD-FAIL on timeout -- an earlier version of this
# script printed "HTTP 500" and then carried on to a success banner, which made the
# terminology half of the content set a check that could not fail.
wait_indexed() {           # $1=url  $2=version  $3=label
  local i
  for i in $(seq 1 90); do
    if curl -s "${BASE%/fhir}/api/indexedCodeSystems" \
         | python3 -c "
import json,sys
u,v=sys.argv[1],sys.argv[2]
sys.exit(0 if any(x['url']==u and x.get('version')==v and x.get('indexStatus')=='OK'
                  for x in json.load(sys.stdin)) else 1)" "$1" "$2"; then
      echo "   $3 -> indexed OK"
      return 0
    fi
    sleep 10
  done
  echo "   $3 -> NOT INDEXED after 15 min" >&2
  return 1
}
curl -s -X POST "${BASE%/fhir}/api/indexCodeSystem?codeSystemId=http%3A%2F%2Fsnomed.info%2Fsct&codeSystemVersion=$(printf %s "$SCT_VERSION" | sed 's|:|%3A|g;s|/|%2F|g')&validate=false" -o /dev/null
curl -s -X POST "${BASE%/fhir}/api/indexCodeSystem?codeSystemId=http%3A%2F%2Floinc.org&codeSystemVersion=$LOINC_VERSION&validate=false" -o /dev/null
wait_indexed "http://snomed.info/sct" "$SCT_VERSION" "SNOMED CT-AU $SCT_VERSION"
wait_indexed "http://loinc.org"       "$LOINC_VERSION" "LOINC $LOINC_VERSION"

echo "== KNOWN REMAINING GAPS (no fixture yet) =="
echo "   https://www.humanservices.gov.au/.../air-vaccine-code-formats  (12 files; real"
echo "     coding system -- codes COMIRN/ENGP/FQUAD appear at vaccineCode.coding)"
echo "   urn:oid:1.2.36.1.2001.1005.17 -- 2 issues on 2 files, NOT 13. The OID appears
     nowhere in the corpus; it is reached through a ValueSet binding, so there is no
     fixture to add. Investigate rather than provision."
echo "   v3-ConfidentialityClassification|2014-03-26 is pinned by the IGs but appears in"
echo "     no THO package scanned (6.0.0-7.3.0)"

echo
echo "== VERIFY: does the server match the manifest? =="
./verify-content.py "$BASE"
echo "== CONTENT LOAD OK =="
