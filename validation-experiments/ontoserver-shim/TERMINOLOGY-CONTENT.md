# Terminology content requirements

The shim is about 200 lines and takes an afternoon to understand. **Loading the right
content into the terminology server is the hard part**, and getting it wrong does not
look like failure — it looks like a working stack that quietly returns different answers.
This document exists because several of the requirements below were discovered the
expensive way.

`load-content.sh` automates all of it. Read this when it fails, when you are provisioning
a different server, or when you want to know why a step is there.

## The invariant

> Both validators must be able to resolve the same terminology, or the comparison
> measures provisioning rather than validation.

This sounds obvious and is easy to violate, because the two arms resolve terminology by
completely different routes:

| | how it gets terminology |
|---|---|
| validator-wrapper | over REST, from whatever `txServer` it is pointed at |
| Ontoserver `$validate` | in-process, from its own loaded content |

So content that Ontoserver resolves natively still has to be **loaded and reachable over
REST** for the wrapper, or the wrapper fails checks that Ontoserver passes and the
difference gets misread as a validator difference.

### The concrete instance, because it invalidated a whole run

`hl7.fhir.r4.core` was originally excluded on the reasoning that Ontoserver resolves base
R4 internally. True — and irrelevant to the wrapper, which needs those value sets over
the wire. `$expand` failed on `encounter-status`, `medication-statement-status` and
`mimetypes`, so the wrapper rejected perfectly valid codes, and the run reported a large
Ontoserver advantage that was really a provisioning gap on the other arm. Production
`tx.dev.hl7.org.au` serves all three.

**Load `hl7.fhir.r4.core` even though Ontoserver does not need it.**

## Packages — the full dependency closure

Fourteen, not the four the IGs name. Declared dependencies pull the rest, and a partial
closure produces unresolvable-profile errors that look like validation findings.

```
hl7.fhir.r4.core#4.0.1              <- required for the WRAPPER arm; see above
hl7.fhir.uv.extensions.r4#5.2.0
hl7.fhir.uv.extensions.r4#5.3.0     <- both versions; different IGs pin different ones
hl7.fhir.uv.smart-app-launch#2.2.0
hl7.fhir.uv.ipa#1.1.0
hl7.fhir.au.base#6.0.0
hl7.fhir.au.base#7.0.0-ballot1
hl7.fhir.au.core#2.0.0
hl7.fhir.au.core#3.0.0-ballot1
hl7.fhir.au.ps#1.0.0
hl7.fhir.au.ereq#1.0.0
hl7.fhir.uv.ips#2.0.0
hl7.fhir.uv.ips#2.0.1
ihe.formatcode.fhir#1.1.0
```

Loaded via `$x-load-package` (tarball, `application/tar+tgz`). Multiple versions of the
same package coexist; both arms handle that correctly.

## Cross-version extensions (xver) — cherry-picked, not loaded whole

`hl7.fhir.uv.xver-r5.r4#0.1.0` **cannot be loaded as a package.** Nine of its 4708
resources carry titles longer than the `varchar(255)` column behind
`structure_definition.title`, and because the load is a single transaction those nine
roll back the other 4699. The failure surfaces as `HAPI-0389 rollback-only` over an
underlying SQLState 22001, which does not obviously say "one title is too long".

`load-xver.py` works around it by cherry-picking only the StructureDefinitions the AU IGs
actually reference — 17 of them. It derives the set at load time rather than hard-coding
it, and treats an absent upstream canonical as a warning, not a failure.

This is an Ontoserver defect, not an xver problem. Any deployment needing the whole
package hits it.

## NCTS — the Australian national content

SNOMED CT-AU and LOINC come from the NCTS syndication feed; the AU FHIR resources come as
a bundle.

```
feed   https://api.healthterminologies.gov.au/syndication/v1/syndication.xml
token  https://api.healthterminologies.gov.au/oauth2/token   (OAuth2 client_credentials)
```

- **SNOMED CT-AU** `http://snomed.info/sct/32506021000036107/version/20260831`
- **LOINC** `2.82`
- **FHIR bundle** version `20260831`, loaded whole — it *is* the AU content

### Load the bundle with `/api/addBundle`, not a FHIR transaction

Two approaches fail, and the second fails **silently**:

| approach | result |
|---|---|
| `POST /fhir` as `transaction` | HTTP 422, `Expected 'batch'` — loud, fine |
| `POST /fhir` as `batch` of PUTs | **HTTP 200** while every entry 422s with `batch operation can only be invoked with GET or POST`. Reports "542 loaded, 0 failed" having loaded nothing |
| `POST /api/addBundle` | works |

The batch case is the dangerous one: the loader declares success and the content is
absent. It was only caught by verifying that a specific code actually resolved
afterwards. **Always verify content after loading, never trust the loader's own report.**

### Indexing is asynchronous

Loading SNOMED/LOINC triggers a background index job that is GB-scale and takes minutes.
An explicit `indexCodeSystem` call issued too early races the syndication sync and returns
HTTP 500 — which earlier versions of the loader printed and then continued to a success
banner.

`load-content.sh` polls `indexStatus=OK` via `wait_indexed()` and hard-fails after 15
minutes rather than proceeding.

## Fixtures — content no package supplies

Ten small resources in `fixtures/`, listed in `manifest.json`.

- `fixtures/tho/*` — five THO CodeSystems the AU IGs pin by exact version. Loaded
  individually rather than as the whole of `hl7.terminology`, which is ~1900 CodeSystems
  and re-inflates the terminology surface the rig exists to hold still.
- `codesystem-anzsic`, `codesystem-cvx`, `codesystem-pbs` — code systems referenced by
  the corpus that no package supplies.
- `codesystem-atc` — **not committed here.** WHO licenses ATC and its own copyright
  statement forbids distribution for commercial purposes, so publishing ~6,900 concepts in
  a public repo would be redistribution. Supply it yourself; see
  `fixtures/codesystem-atc.json.MISSING.md`. Everything works without it, except that ATC
  codes read as unknown — a provisioning difference, not a validator one.
- `codesystem-bcp13-mimetypes` — `urn:ietf:bcp:13`, 1848 concepts, `content=fragment`.
  R4's `mimetypes` value set enumerates nothing, so a server with no BCP-13 content
  cannot answer `$validate-code` for a mime type at all.

## Verification — the step that is not optional

```bash
./verify-content.py http://localhost:8080/fhir
```

It derives its expectations from the package tarballs and the cached NCTS bundle rather
than a hand-written allowlist, and honours `server_native_prefixes` for canonicals the
server supplies itself. Expected on a correct load:

```
CodeSystem           1190 / 1190
ValueSet             1666 / 1667
StructureDefinition  1226 / 2624     (packages declare more than the corpus reaches)
ConceptMap            122 /  123
```

A hand-written allowlist was tried first and produced a false positive on
`artifact-version-policy-codes`, which legitimately ships in
`hl7.fhir.uv.extensions.r4#5.2.0`. Derive, do not enumerate.

## Credentials

NCTS credentials are read from the environment only — `NCTS_CLIENT_ID` and
`NCTS_CLIENT_SECRET` — and are never written to any file in this directory. `compose.yml`
interpolates them, so compose needs them present:

```bash
docker compose --env-file /path/to/your/.env up -d
```

Without the env file Ontoserver starts with empty credentials and the syndication sync
fails in a way that is not obvious until content turns out to be missing.

## Which server holds this

Any FHIR R4 terminology server can serve the wrapper arm. The Ontoserver arm needs a build
carrying the new validation engine (`ontoserver.validator.engine=new`), which is not in a
released Ontoserver at time of writing — `assert-engine.sh` checks the running server is
actually on it, because the flag silently falls back to the old engine otherwise.

For reference, all three official AU terminology servers are Ontoserver:
`tx.ontoserver.csiro.au`, `tx.hl7.org.au`, and `tx.dev.hl7.org.au` (the one Inferno uses).
