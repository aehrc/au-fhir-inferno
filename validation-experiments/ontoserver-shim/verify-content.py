#!/usr/bin/env python3
"""Assert the running server holds exactly the manifest's content -- nothing extra.

  ./verify-content.py http://localhost:8080/fhir

DESIGN: the allowlist is DERIVED from the package tarballs actually named in
load-content.sh, not hand-written. A hand-written list gets this wrong in both
directions: it fired on `artifact-version-policy-codes`, which legitimately ships inside
hl7.fhir.uv.extensions.r4, and it would have said nothing about a stray CodeSystem in
any other namespace. Deriving it means the check covers every CodeSystem on the server.

The failure this exists to catch: `docker compose rm` does NOT remove the db service's
ANONYMOUS volume, so a "clean rebuild" once left ~1900 surplus THO resources in place and
every number taken afterwards would have been measured against unintended content.
"""
import io, json, os, sys, tarfile, urllib.request

CACHE = os.path.expanduser("~/.cache/au-ct27-packages")


TYPES = ("CodeSystem", "ValueSet", "StructureDefinition", "ConceptMap")


def package_canonicals(spec):
    """{resourceType: {canonical urls}} for a published package tarball.

    DESIGN: all four conformance types, not just CodeSystem. An earlier version checked
    CodeSystems alone, which said nothing about the 3158 StructureDefinitions on the
    server -- and StructureDefinitions are where the bulk actually is (629 of the first
    1016 came from one extensions package).
    """
    name, version = spec.split("#")
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{name}-{version}.tgz")
    if not os.path.exists(path):
        url = f"https://packages.fhir.org/{name}/{version}"
        with urllib.request.urlopen(url, timeout=120) as r, open(path, "wb") as fh:
            fh.write(r.read())
    out = {t: set() for t in TYPES}
    with tarfile.open(path) as tf:
        for m in tf.getmembers():
            if not (m.isfile() and m.name.endswith(".json")):
                continue
            try:
                doc = json.load(tf.extractfile(m))
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("resourceType") in TYPES \
                    and doc.get("url"):
                out[doc["resourceType"]].add(doc["url"])
    return out


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/fhir"
    manifest = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "manifest.json")))

    allowed = {t: set() for t in TYPES}
    for spec in manifest["packages"]:
        for t, urls in package_canonicals(spec).items():
            allowed[t] |= urls
    # cherry-picked packages contribute ONLY the named resourceType; anything else from
    # them on the server means the whole package got loaded by mistake.
    for spec, rule in manifest.get("cherry_picked", {}).items():
        allowed[rule["resourceType"]] |= package_canonicals(spec)[rule["resourceType"]]
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in manifest["fixtures"]:
        doc = json.load(open(os.path.join(here, rel)))
        allowed[doc["resourceType"]].add(doc["url"])
    allowed["CodeSystem"] |= set(manifest["terminology"])

    # DESIGN: the NCTS bundle's canonicals are read from the bundle itself, not matched by
    # a hostname prefix. NCTS ships its own copies of CORE FHIR canonicals
    # (http://hl7.org/fhir/administrative-gender, .../observation-status, ...), so a
    # healthterminologies.gov.au prefix rule misses ~60 of them and reports false surplus.
    # Same lesson as the THO allowlist: derive from the source, never guess the shape.
    nb = manifest.get("ncts_bundle")
    if nb:
        bpath = os.path.join(CACHE, f"ncts-fhir-bundle-r4-{nb['version']}.json")
        if os.path.exists(bpath):
            for e in json.load(open(bpath)).get("entry", []):
                r = e.get("resource", {})
                if r.get("resourceType") in TYPES and r.get("url"):
                    allowed[r["resourceType"]].add(r["url"])
        else:
            print(f"  !! NCTS bundle not cached at {bpath}; its canonicals cannot be "
                  f"allowlisted and will show as surplus")

    root = base.rstrip("/")
    ok = True
    for t in TYPES:
        on_server = set()
        nxt = f"{root}/{t}?_elements=url&_count=1000"
        while nxt:
            with urllib.request.urlopen(nxt, timeout=120) as r:
                bundle = json.load(r)
            for e in bundle.get("entry", []):
                u = e["resource"].get("url")
                if u:
                    on_server.add(u)
            nxt = next((l["url"] for l in bundle.get("link", [])
                        if l.get("relation") == "next"), None)
        native = tuple(manifest.get("server_native_prefixes", []))
        surplus = sorted(u for u in on_server - allowed[t]
                         if not (native and u.startswith(native)))
        print(f"  {t:20s} on server {len(on_server):5d}   manifest allows "
              f"{len(allowed[t]):5d}")
        if surplus:
            ok = False
            print(f"    !! SURPLUS {len(surplus)}:")
            for u in surplus[:8]:
                print(f"         {u}")
            if len(surplus) > 8:
                print(f"         ... and {len(surplus) - 8} more")

    with urllib.request.urlopen(root.replace("/fhir", "") + "/api/indexedCodeSystems",
                                timeout=60) as r:
        indexed = {x["url"] for x in json.load(r)}
    missing = sorted(set(manifest["terminology"]) - indexed)
    if missing:
        ok = False
        print(f"  !! MISSING required terminology: {missing}")

    if ok:
        print("  content set matches the manifest")
    else:
        print("  !! Rebuild with `docker compose down -v` -- `compose rm` leaves the db")
        print("     service's ANONYMOUS volume in place and content survives a teardown.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
