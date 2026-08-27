#!/bin/bash
# Assert an Ontoserver is running the NEW (validodo) validation engine.
#
#   ./assert-engine.sh http://localhost:8080/fhir
#
# DESIGN: `ontoserver.validator.engine` is not exposed by /api/version, /fhir/metadata,
# or actuator, and PR #1178's classes are not in the develop checkout -- so this cannot
# be read, only observed. A stock container defaults to 'hapi' and would silently
# measure the wrong engine.
#
# Discriminator, verified 2026-08-26 against identical images on one DB (38/38 files
# differed): dom-6 'A resource should have narrative' is INFORMATION on the new engine
# and WARNING on hapi. Chosen over the absent-code-silence behaviour, which was checked
# and does NOT discriminate on this build -- both engines report the unknown code.
set -euo pipefail
BASE="${1:?usage: ./assert-engine.sh <fhir-base>}"

OUT=$(curl -s -X POST "$BASE/Patient/\$validate" \
  -H 'Content-Type: application/fhir+json' \
  --data-binary '{"resourceType":"Patient","id":"engine-probe"}')

SEV=$(printf '%s' "$OUT" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for i in d.get("issue",[]):
    t=(i.get("diagnostics") or i.get("details",{}).get("text","") or "")
    if "dom-6" in t: print(i.get("severity")); break
else: print("absent")')

case "$SEV" in
  information) echo "engine=new   (dom-6 reported as information)  [$BASE]"; exit 0 ;;
  warning)     echo "ENGINE=HAPI  (dom-6 reported as warning) -- NOT the new engine [$BASE]"; exit 1 ;;
  *)           echo "INCONCLUSIVE (dom-6 not reported at all; probe assumption broken) [$BASE]"; exit 2 ;;
esac
