# Ontoserver `$validate` as an Inferno validator backend

An experiment: can au-fhir-inferno use Ontoserver's FHIR `$validate` operation instead of
the validator-wrapper?

This directory holds the working substitution — a shim that speaks the validator-wrapper's
HTTP protocol and answers it from Ontoserver — plus the content-loading rig needed to make
the two comparable. It is an experiment branch, not a proposal to merge.

## The substitution point is one environment variable

Inferno's `fhir_resource_validator` DSL talks to whatever `FHIR_RESOURCE_VALIDATOR_URL`
names, over the wrapper's HTTP protocol:

```yaml
inferno_worker:
  environment:
    - FHIR_RESOURCE_VALIDATOR_URL=http://validator-api:3500   # -> point at the shim
```

Anything speaking that protocol drops in with no code change. `shim.py` is ~200 lines and
needs no fork of inferno-core. That is the cheapest of three possible routes; the others
are a `Validator` subclass in `patches.rb`, or a new backend upstream in inferno-core.

Worth noting independently of any measurement: inferno-core keys validator sessions on
`(test_suite_id, validator_name, suite_options)`, and `patches.rb` carries a ~30-line
`FixValidatorSessionKeyCollision` workaround for collisions in that key. A stateless
`$validate` has no session to collide with, so that class of bug cannot arise.

## What is here

```
shim.py                 the shim: wrapper protocol in, $validate out
run-arm.py              driver -- points at either the shim or a real wrapper
compose.yml             db + Ontoserver + TLS terminator + wrapper
load-content.sh         one-shot content load (calls the loaders below)
load-igs.py             package loading via $x-load-package
load-ncts.py            NCTS syndication + FHIR bundle
load-xver.py            cross-version SDs, cherry-picked (see TERMINOLOGY-CONTENT.md)
verify-content.py       asserts loaded content matches manifest.json
assert-engine.sh        asserts the server really is on the new validation engine
manifest.json           the content set, declared
fixtures/               content no package supplies
tls/                    TLS terminator; run tls/gen-cert.sh first
```

**[TERMINOLOGY-CONTENT.md](TERMINOLOGY-CONTENT.md) is the important document.** The shim is
easy; provisioning the terminology server correctly is not, and getting it wrong produces
a stack that looks healthy and quietly answers differently.

## Running it

```bash
# 1. TLS cert for the terminator (generated, never committed)
./tls/gen-cert.sh

# 2. an Ontoserver image carrying the new validation engine
export ONTO_IMAGE=aehrc/ontoserver:<a-build-with-the-new-engine>

# 3. bring it up -- NCTS credentials come from your own env file
docker compose --env-file /path/to/.env up -d

# 4. load content (~30-60 min, mostly SNOMED/LOINC indexing) and verify
./load-content.sh http://localhost:8080/fhir

# 5. confirm the engine is what you think it is
./assert-engine.sh
```

`ONTO_PORT` and `TX_TLS_PORT` override the published ports. Worth setting `ONTO_PORT` —
8080 collides with any locally-running Spring Boot app, which is a common way to lose an
afternoon.

### Test data

The corpus is not vendored here. It is
[hl7au/au-fhir-test-data](https://github.com/hl7au/au-fhir-test-data), pinned for these
runs at `ddb96e935ee71866b48d3b49291609868a414baa` (2026-08-24):

```bash
git clone https://github.com/hl7au/au-fhir-test-data.git
git -C au-fhir-test-data checkout ddb96e935ee71866b48d3b49291609868a414baa
```

200 resources across au-core (92), connected-care (72), au-erequesting (34) and
au-patient-summary (2).

## What the shim does and does not do

**Does:** translate the wrapper's `{cliContext, filesToValidate, sessionId}` request into
FHIR `$validate`, map the returned OperationOutcome back into the wrapper's
`{sessionId, outcomes[{fileInfo, issues[{level, message, location}]}]}` shape, and gate on
the requested IG being present rather than validating against whatever happens to be
loaded.

**Does not:** fetch packages on demand. The wrapper downloads a session's declared IGs at
validation time; Ontoserver validates against loaded content. That difference is why the
content set has to be provisioned deliberately, and why the shim fails loudly rather than
silently validating against the wrong thing.

Two protocol details that cost time:

- The wrapper build in au-fhir-inferno's `compose.yml`
  (`ghcr.io/beda-software/validator-wrapper`) reads `cliContext`. Sending
  `validationContext` gets a bare `NullPointerException` and an HTTP 500 with no body.
- That image overrides Temurin's cacert entrypoint, so `USE_SYSTEM_CA_CERTS` and
  `/certificates` do nothing. Trusting a local TLS cert requires a build-time
  `keytool -importcert` — see `tls/Dockerfile.wrapper`. (This *does* work on
  markiantorno's image; the two differ.)

## Status

Experimental. The comparison results this rig produced are not published here — ask
Jim Steel (CSIRO) or see the evaluation write-up.
