#!/usr/bin/env python3
"""Preload the NCTS FHIR resource bundle (R4) via Ontoserver's /api/addBundle.

  ./load-ncts.py http://localhost:8080/fhir [20260831]

Credentials come from NCTS_CLIENT_ID / NCTS_CLIENT_SECRET in the environment (the same
the same env file the compose file reads). Nothing is written to disk
here except the downloaded bundle, and no credential is ever printed.

WHY THE WHOLE BUNDLE, when THO and xver are deliberately cherry-picked: this is the
Australian national terminology set delivered by NCTS syndication, and it is what a real
HL7 AU Ontoserver (tx.dev.hl7.org.au) actually holds. It is the realistic configuration,
not surplus. THO and xver are upstream dependency packages whose bulk is irrelevant to AU
content and inflates the resolution surface under test; this bundle IS the AU content.

542 resources: 346 ValueSets, 147 CodeSystems, 49 ConceptMaps. It supplies the AIR vaccine
CodeSystem at exactly the URI the corpus uses, so `$lookup COMIRN` resolves to Comirnaty
and 12 files' worth of unknown-system warnings go away.

DESIGN -- /api/addBundle, not a FHIR batch and not 542 PUTs.
  * A `transaction` Bundle is rejected outright ("Expected 'batch'").
  * A `batch` Bundle of PUTs returns HTTP 200 while every entry fails 422 with "batch
    operation can only be invoked with GET or POST" -- total success reported, nothing
    loaded. That is the exact failure shape this rig keeps producing, so it is called out
    here: a 200 on a batch says nothing about the entries.
  * addBundle is purpose-built, single-call, and idempotent -- it reports `duplicate` for
    resources already present rather than erroring or double-loading.
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request

FEED = "https://api.healthterminologies.gov.au/syndication/v1/syndication.xml"
TOKEN_URL = "https://api.healthterminologies.gov.au/oauth2/token"
CACHE = os.path.expanduser("~/.cache/au-ct27-packages")


def token():
    cid, sec = os.environ.get("NCTS_CLIENT_ID"), os.environ.get("NCTS_CLIENT_SECRET")
    if not (cid and sec):
        sys.exit("  !! NCTS_CLIENT_ID / NCTS_CLIENT_SECRET not in the environment; "
                 "set them in your environment first")
    data = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "client_id": cid, "client_secret": sec}).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data),
                                timeout=60) as r:
        return json.load(r)["access_token"]


def bundle_href(version):
    with urllib.request.urlopen(FEED, timeout=60) as r:
        feed = r.read().decode("utf-8", "replace")
    for entry in re.findall(r"<entry>.*?</entry>", feed, re.S):
        if f"fhir-resource-bundle-r4/version/{version}" not in entry:
            continue
        m = re.search(r'href="([^"]+\.json)"', entry)
        if m:
            return m.group(1)
    sys.exit(f"  !! no NCTS R4 bundle for version {version} in the feed")


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/fhir"
    version = sys.argv[2] if len(sys.argv) > 2 else "20260831"
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"ncts-fhir-bundle-r4-{version}.json")

    if not os.path.exists(path):
        href = bundle_href(version)
        req = urllib.request.Request(href, headers={"Authorization": f"Bearer {token()}"})
        with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as fh:
            fh.write(r.read())
        print(f"  downloaded NCTS R4 bundle {version} ({os.path.getsize(path)//1024} KB)")
    else:
        print(f"  using cached NCTS R4 bundle {version}")

    payload = open(path, "rb").read()
    admin = base.rstrip("/").replace("/fhir", "") + "/api/addBundle"
    req = urllib.request.Request(admin, data=payload, method="POST",
                                 headers={"Content-Type": "application/fhir+json"})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            oo = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"  [FAIL] HTTP {e.code}: {e.read()[:300].decode('utf-8','replace')}",
              file=sys.stderr)
        return 1

    issues = oo.get("issue", [])
    added = sum(1 for i in issues if i.get("code") != "duplicate"
                and i.get("severity") == "information")
    dup = sum(1 for i in issues if i.get("code") == "duplicate")
    errs = [i for i in issues if i.get("severity") in ("error", "fatal")]
    print(f"  addBundle: {len(issues)} issue(s) -- {dup} already present, "
          f"{len(errs)} error(s)")
    for i in errs[:5]:
        print(f"     {i.get('diagnostics','')[:160]}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
