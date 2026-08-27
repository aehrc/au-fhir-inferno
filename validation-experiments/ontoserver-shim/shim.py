#!/usr/bin/env python3
"""Speak the validator-wrapper's HTTP protocol; answer from Ontoserver's $validate.

  ./shim.py --onto http://localhost:8080/fhir --port 3500 --igs hl7.fhir.au.core#2.0.0

Drops in where au-fhir-inferno's `validator-api` service sits, so Inferno needs no
change: FHIR_RESOURCE_VALIDATOR_URL just points here instead.

REQUEST   {validationContext|cliContext: {igs, profiles, ...},
           filesToValidate: [{fileName, fileContent, fileType}], sessionId}
RESPONSE  {sessionId, outcomes: [{fileInfo, issues: [{level, message, location}]}]}

Contract verified against inferno-core lib/inferno/dsl/fhir_resource_validation.rb:
`calculate_severity` upcases raw_issue['level'] and treats anything that is not
ERROR/FATAL/WARNING as info; `extract_location` reads raw_issue['location'];
`format_message` reads raw_issue['message']; issues are read from outcomes[0].issues.

DESIGN -- the IG gate. The wrapper DOWNLOADS whatever `igs` names, per session.
Ontoserver fetches nothing; packages must be pushed in first (see load-igs.py). So a
naive shim would validate against whatever the server happens to hold and return a
confident, green answer about a context nobody specified -- a check that cannot fail.
Before validating, this asserts the requested profile actually resolves in Ontoserver
and REFUSES if it does not. An unloaded IG is reported as an error, never as a pass.
"""
import argparse, json, sys, threading, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
_profile_cache = {}
_cache_lock = threading.Lock()
SEVERITY_TO_LEVEL = {"fatal": "FATAL", "error": "ERROR",
                     "warning": "WARNING", "information": "INFORMATION"}


def onto_get(path):
    req = urllib.request.Request(ARGS.onto.rstrip("/") + path,
                                 headers={"Accept": "application/fhir+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def profile_present(canonical):
    """True if Ontoserver holds a StructureDefinition at this canonical.

    INVARIANT: a False here must never fall through to a validation call -- that is the
    whole point of the gate.
    """
    base = canonical.split("|")[0]
    with _cache_lock:
        if base in _profile_cache:
            return _profile_cache[base]
    try:
        b = onto_get("/StructureDefinition?url=" + urllib.parse.quote(base, safe="") +
                     "&_elements=id,version&_count=1")
        ok = (b.get("total", 0) or len(b.get("entry", []))) > 0
    except Exception:
        ok = False
    with _cache_lock:
        _profile_cache[base] = ok
    return ok


def validate(resource_text, profile):
    res = json.loads(resource_text)
    rtype = res.get("resourceType")
    if not rtype:
        raise ValueError("payload has no resourceType")
    url = f"{ARGS.onto.rstrip('/')}/{rtype}/$validate"
    if profile:
        url += "?profile=" + urllib.parse.quote(profile, safe="")
    req = urllib.request.Request(url, data=resource_text.encode(), method="POST",
                                 headers={"Content-Type": "application/fhir+json",
                                          "Accept": "application/fhir+json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return issue_outcome("ERROR", f"Ontoserver HTTP {e.code}: {body[:300]}")


def issue_outcome(level, message, location="unknown"):
    return {"__shim_issues": [{"level": level, "message": message, "location": location}]}


def to_wrapper_issues(outcome):
    if "__shim_issues" in outcome:
        return outcome["__shim_issues"]
    out = []
    for i in outcome.get("issue", []):
        loc = (i.get("expression") or i.get("location") or ["unknown"])[0]
        msg = i.get("diagnostics") or (i.get("details") or {}).get("text") or i.get("code", "")
        out.append({"level": SEVERITY_TO_LEVEL.get(i.get("severity"), "INFORMATION"),
                    "message": msg, "location": loc})
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        if ARGS.verbose:
            super().log_message(*a)

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", ""):
            self._send(200, {"status": "ok", "onto": ARGS.onto, "igs_declared": ARGS.igs})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/validate":
            return self._send(404, {"error": "not found"})
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            req = json.loads(raw)
        except Exception as e:
            return self._send(400, {"error": f"bad request: {e}"})

        ctx = req.get("validationContext") or req.get("cliContext") or {}
        files = req.get("filesToValidate") or []
        session = req.get("sessionId") or "shim-session"
        profiles = ctx.get("profiles") or []
        profile = profiles[0] if profiles else None

        outcomes = []
        for f in files:
            issues = self._issues_for(f, ctx, profile)
            outcomes.append({"fileInfo": {"fileName": f.get("fileName"),
                                          "fileContent": "", "fileType": f.get("fileType")},
                             "issues": issues})
        self._send(200, {"sessionId": session, "outcomes": outcomes})

    def _issues_for(self, f, ctx, profile):
        # THE GATE -- refuse rather than validate against a context we do not hold.
        declared = set(ARGS.igs or [])
        requested = set(ctx.get("igs") or [])
        missing_igs = sorted(requested - declared) if declared else []
        if missing_igs:
            return [{"level": "ERROR", "location": "shim",
                     "message": ("Shim refused: IG(s) not loaded into Ontoserver: "
                                 f"{', '.join(missing_igs)}. Ontoserver does not download "
                                 "packages; load them with load-igs.py first.")}]
        if profile and not profile_present(profile):
            return [{"level": "ERROR", "location": "shim",
                     "message": (f"Shim refused: profile {profile} does not resolve in "
                                 "Ontoserver, so validation would not have checked it.")}]
        try:
            return to_wrapper_issues(validate(f.get("fileContent", ""), profile))
        except Exception as e:
            return [{"level": "ERROR", "location": "shim", "message": f"Shim error: {e}"}]


def main():
    global ARGS
    p = argparse.ArgumentParser()
    p.add_argument("--onto", required=True, help="Ontoserver FHIR base, e.g. http://localhost:8080/fhir")
    p.add_argument("--port", type=int, default=3500)
    p.add_argument("--igs", nargs="*", default=[], help="packages known to be loaded (name#version)")
    p.add_argument("--verbose", action="store_true")
    ARGS = p.parse_args()
    print(f"shim -> {ARGS.onto}  listening :{ARGS.port}  igs declared: {ARGS.igs or '(gate off)'}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
