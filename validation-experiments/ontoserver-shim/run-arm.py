#!/usr/bin/env python3
"""Drive one wrapper-protocol arm over the corpus; emit JSONL that issues.py can load.

  ./run-arm.py --url http://localhost:3500 --out ../arms/local-wrapper.jsonl   # real wrapper
  ./run-arm.py --url http://localhost:3501 --out ../arms/local-shim.jsonl      # Ontoserver

DESIGN: both arms speak the SAME protocol -- that is the whole premise of the shim -- so
the two runs differ ONLY in the URL. Any per-arm special-casing here would quietly become
part of the measured difference, so there is none.

DESIGN: sessionId is reused across the run, which is how Inferno actually drives the
wrapper (a session per (suite, validator, options), not per resource). Cold cost is
therefore paid once, in file 1, and reported separately rather than smeared over the
corpus. Pass --fresh-session to get a new session per file instead.
"""
import argparse, json, os, sys, time, urllib.error, urllib.request

# Wrapper `level` -> FHIR severity. Mirrors inferno-core calculate_severity: anything
# that is not ERROR/FATAL/WARNING is treated as information.
LEVEL_TO_SEVERITY = {"FATAL": "fatal", "ERROR": "error", "WARNING": "warning"}


def post(url, payload, timeout):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url + "/validate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r), r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--corpus", default="../corpus.tsv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--igs", default="")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--fresh-session", action="store_true")
    ap.add_argument("--session-file", default="",
                    help="persist/reuse the wrapper sessionId across invocations")
    ap.add_argument("--tx", default="",
                    help="terminology server URL passed in cliContext")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N corpus rows (smoke tests)")
    a = ap.parse_args()

    root = os.path.dirname(os.path.abspath(a.corpus))
    rows = []
    with open(a.corpus) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            if line.strip():
                rows.append(dict(zip(header, line.rstrip("\n").split("\t"))))

    if a.limit:
        rows = rows[:a.limit]

    igs = [x for x in a.igs.split(",") if x]
    # DESIGN: reuse the session ACROSS runs, not just within one. The wrapper caches
    # terminology per session, so starting a fresh session for the measured pass would
    # hand it a cold cache while Ontoserver's server-wide cache stayed warm from the warm
    # pass -- biasing the comparison against the wrapper, the exact mirror of the bias the
    # warm pass exists to remove. In production their session never expires
    # (SESSION_CACHE_DURATION=-1), so persisting it is also the faithful configuration.
    session = None
    if a.session_file and os.path.exists(a.session_file) and not a.fresh_session:
        session = open(a.session_file).read().strip() or None
        if session:
            print(f"  reusing session {session[:12]}...")
    written = 0
    with open(a.out, "w") as out:
        for i, row in enumerate(rows, 1):
            # corpus.tsv `rel` is relative to the directory holding corpus.tsv
            path = os.path.join(root, row["rel"])
            content = open(path).read()
            # CONSTRAINT: beda-software's build reads `cliContext` and throws a bare
            # NullPointerException (HTTP 500, no body) on `validationContext` -- 200/200
            # requests failed that way before this was found. The shim accepts either, so
            # `cliContext` is what keeps BOTH arms receiving a byte-identical request,
            # which is the entire premise of the comparison.
            ctx = {"igs": igs,
                   "profiles": [row["profile"]] if row.get("profile") else []}
            if a.tx:
                ctx["txServer"] = a.tx
            payload = {"cliContext": ctx,
                       "filesToValidate": [{"fileName": os.path.basename(row["rel"]),
                                            "fileContent": content, "fileType": "json"}]}
            if session and not a.fresh_session:
                payload["sessionId"] = session

            t0 = time.time()
            err = None
            try:
                doc, status = post(a.url, payload, a.timeout)
            except urllib.error.HTTPError as e:
                doc, status, err = {}, e.code, e.read()[:400].decode("utf-8", "replace")
            except Exception as e:
                doc, status, err = {}, 0, f"{type(e).__name__}: {e}"
            ms = (time.time() - t0) * 1000.0

            if not a.fresh_session and doc.get("sessionId"):
                session = doc["sessionId"]

            outcomes = doc.get("outcomes") or []
            raw = outcomes[0].get("issues", []) if outcomes else []
            issues = [{"severity": LEVEL_TO_SEVERITY.get((it.get("level") or "").upper(),
                                                         "information"),
                       "expression": [it.get("location")] if it.get("location") else [],
                       "diagnostics": it.get("message", "")}
                      for it in raw]

            # DESIGN: emitted as a FHIR OperationOutcome so issues.py/diff.py -- already
            # calibrated on the r4 runs -- load these arms with no changes at all.
            rec = {"rel": row["rel"], "group": row["group"],
                   "resourceType": row["resourceType"], "profile": row.get("profile", ""),
                   "ms": round(ms, 1), "status": status, "cold": (i == 1),
                   "outcome": {"resourceType": "OperationOutcome", "issue": issues}}
            # INVARIANT: a record with status != 200 must be visibly broken downstream.
            # A failed request yields ZERO issues, and "zero error-severity issues" is
            # exactly how verdict.py spells PASS -- so an unrecorded failure scores as a
            # clean pass. `if err:` was not enough: HTTPError.read() can return an empty
            # body, err is then "" and falsy, and the 500 vanished from the record.
            if status != 200 or err:
                rec["error"] = err or f"HTTP {status} with no body"
            out.write(json.dumps(rec) + "\n")
            out.flush()
            written += 1
            if i % 25 == 0 or err:
                print(f"  {i}/{len(rows)}  {row['group']}/{os.path.basename(row['rel'])}"
                      f"  {ms:.0f}ms" + (f"  ERR {err[:80]}" if err else ""), flush=True)
            if a.sleep:
                time.sleep(a.sleep)

    if a.session_file and session:
        with open(a.session_file, "w") as fh:
            fh.write(session)
    print(f"wrote {written} -> {a.out}")


if __name__ == "__main__":
    main()
