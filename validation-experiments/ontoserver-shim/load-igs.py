#!/usr/bin/env python3
"""Load FHIR IG packages into Ontoserver via $x-load-package.

  ./load-igs.py http://localhost:8080/fhir hl7.fhir.au.core#2.0.0 hl7.fhir.au.base#6.0.0

DESIGN: the validator-wrapper downloads whatever `validationContext.igs` names, per
session. Ontoserver fetches nothing -- packages must be pushed in ahead of time. That
asymmetry is why shim.py gates on IG presence: a shim validating against "whatever
happens to be loaded" gives confident answers about a context nobody asked for.

$x-load-package takes the raw package tarball (application/tar+tgz) on the Ontoserver
base URL and dedupes on url+version+id, so re-running is safe.
"""
import sys, urllib.request, urllib.error, json

REGISTRY = "https://packages.fhir.org"


def load(base, name, version):
    try:
        with urllib.request.urlopen(f"{REGISTRY}/{name}/{version}", timeout=300) as r:
            blob = r.read()
    except Exception as e:
        return False, f"fetch failed: {e}"

    req = urllib.request.Request(f"{base}/$x-load-package", data=blob, method="POST",
                                 headers={"Content-Type": "application/tar+tgz"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            body = r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
    except Exception as e:
        return False, str(e)[:300]

    try:
        oo = json.loads(body)
        sev = [i.get("severity") for i in oo.get("issue", [])]
        errs = [i for i in oo.get("issue", []) if i.get("severity") in ("error", "fatal")]
        summary = f"{len(sev)} issue(s); {len(errs)} error(s), {len(blob)//1024} KB posted"
        if errs:
            summary += " -> " + (errs[0].get("diagnostics") or "")[:160]
        return not errs, summary
    except Exception:
        return True, f"{len(blob)//1024} KB posted; non-JSON response"


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    base, specs = sys.argv[1].rstrip("/"), sys.argv[2:]
    bad = 0
    for spec in specs:
        name, _, version = spec.partition("#")
        ok, msg = load(base, name, version)
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}#{version}: {msg}")
        bad += 0 if ok else 1
    print(f"\n{len(specs)-bad}/{len(specs)} packages loaded")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
