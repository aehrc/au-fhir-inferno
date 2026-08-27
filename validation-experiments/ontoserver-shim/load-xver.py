#!/usr/bin/env python3
"""Cherry-pick the cross-version extension SDs the AU IGs actually reference.

  ./load-xver.py http://localhost:8080/fhir [0.1.0]

WHY NOT THE WHOLE PACKAGE. hl7.fhir.uv.xver-r5.r4#0.1.0 is 4708 resources, and only 1426
of them are StructureDefinitions -- the rest is 2434 ValueSets, 645 CodeSystems and 201
ConceptMaps. Loading it whole took this server from 93 CodeSystems to 788, which is the
same mistake the lean-THO decision exists to avoid: it enlarges the terminology
resolution surface that the rig is trying to measure. The AU IGs reference exactly 18 of
those StructureDefinitions and no xver terminology at all.

The 18 are DERIVED here by scanning the loaded IG tarballs for
http://hl7.org/fhir/5.0/StructureDefinition/... references, not hardcoded, so adding an
IG to load-content.sh automatically widens the set.

SEPARATE FINDING -- Ontoserver cannot load this package whole even if you want to:
`structure_definitionr4.title` is varchar(255) and 9 resources carry a longer title (up
to 754 chars). The insert fails with SQLState 22001 and, because $x-load-package is one
transaction, those 9 roll back the other 4699. The validator-wrapper ingests the package
without complaint. Reported; not fixed here -- this session does not own Ontoserver code.
Cherry-picking sidesteps it: none of the 18 has an over-length title.
"""
import glob, io, json, os, re, sys, tarfile, time, urllib.error, urllib.parse, urllib.request

CACHE = os.path.expanduser("~/.cache/au-ct27-packages")
XVER_RE = re.compile(rb"http://hl7\.org/fhir/5\.0/StructureDefinition/[A-Za-z0-9._-]+")
LIMIT = 255


def cached_package(spec):
    """Download a package tarball into CACHE if absent; return its path."""
    name, version = spec.split("#")
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{name}-{version}.tgz")
    if not os.path.exists(path):
        url = f"https://packages.fhir.org/{name}/{version}"
        with urllib.request.urlopen(url, timeout=300) as r, open(path, "wb") as fh:
            fh.write(r.read())
    return path


def wanted_canonicals():
    """Union of xver canonicals referenced by every package named in manifest.json.

    DESIGN: the package list comes from the manifest rather than from whatever happens to
    be sitting in CACHE. load-igs.py streams tarballs straight to Ontoserver without
    caching them, so a CACHE scan silently found nothing and the derived set came back
    empty -- which would have loaded zero extensions while reporting success.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = json.load(open(os.path.join(here, "manifest.json")))
    urls = set()
    for spec in manifest["packages"]:
        try:
            with tarfile.open(cached_package(spec)) as tf:
                for m in tf.getmembers():
                    if m.isfile() and m.name.endswith(".json"):
                        urls |= {u.decode() for u in
                                 XVER_RE.findall(tf.extractfile(m).read())}
        except Exception as e:
            print(f"  !! could not scan {spec}: {e}", file=sys.stderr)
    return urls


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/fhir").rstrip("/")
    version = sys.argv[2] if len(sys.argv) > 2 else "0.1.0"
    os.makedirs(CACHE, exist_ok=True)
    src = os.path.join(CACHE, f"hl7.fhir.uv.xver-r5.r4-{version}.tgz")
    if not os.path.exists(src):
        url = f"https://packages.fhir.org/hl7.fhir.uv.xver-r5.r4/{version}"
        with urllib.request.urlopen(url, timeout=300) as r, open(src, "wb") as fh:
            fh.write(r.read())

    want = wanted_canonicals()
    if not want:
        print("  !! no xver canonicals referenced by any manifest package",
              file=sys.stderr)
        return 1

    found, posted, failed = {}, 0, 0
    with tarfile.open(src) as tf:
        for m in tf.getmembers():
            if not (m.isfile() and m.name.endswith(".json")):
                continue
            try:
                doc = json.load(tf.extractfile(m))
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("resourceType") == "StructureDefinition" \
                    and doc.get("url") in want:
                found[doc["url"]] = doc

    print(f"  AU IGs reference {len(want)} xver canonical(s); {len(found)} found in the "
          f"package (of 4708 resources)")
    for url, doc in sorted(found.items()):
        for f in ("title", "name", "publisher"):
            v = doc.get(f)
            if isinstance(v, str) and len(v) > LIMIT:
                doc[f] = v[:LIMIT]
                print(f"     truncated {f} on {doc.get('id')} ({len(v)} chars)")
        body = json.dumps(doc).encode()
        req = urllib.request.Request(
            f"{base}/StructureDefinition/{urllib.parse.quote(doc['id'], safe='')}",
            data=body, method="PUT",
            headers={"Content-Type": "application/fhir+json"})
        # Ontoserver returns 503 "Transient lock contention" while it is still indexing
        # the packages loaded moments earlier. Retry rather than record a spurious gap.
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    print(f"     {doc['id']:<52s} -> HTTP {r.status}")
                    posted += 1
                    break
            except urllib.error.HTTPError as e:
                body = e.read()[:160].decode("utf-8", "replace")
                if e.code == 503 and attempt < 5:
                    time.sleep(5 * (attempt + 1))
                    continue
                print(f"     {doc['id']:<52s} -> HTTP {e.code} {body}", file=sys.stderr)
                failed += 1
                break

    # DESIGN: an unresolved canonical is a WARNING, not a failure. The AU IGs reference
    # extension-Encounter.participant.actor, which does not exist anywhere in their own
    # declared dependency (xver-r5.r4#0.1.0) -- so the validator-wrapper cannot resolve it
    # either and both arms are blind to it alike. Exiting non-zero here aborted the rest
    # of the content load under `set -e`, which is a real failure caused by a non-failure.
    for url in sorted(want - set(found)):
        print(f"  !! referenced by an AU IG but ABSENT from the package (the wrapper "
              f"cannot resolve it either): {url}", file=sys.stderr)

    print(f"  {posted} loaded, {failed} failed, {len(want) - len(found)} absent upstream")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
