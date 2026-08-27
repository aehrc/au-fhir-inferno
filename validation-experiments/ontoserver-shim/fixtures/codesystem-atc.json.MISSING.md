# `codesystem-atc.json` is deliberately absent

The ATC CodeSystem (`http://www.whocc.no/atc`, ~6,900 concepts) is **not committed to this
public repository**. Its own copyright statement says:

> WHO Collaborating Centre for Drug Statistics Methodology, Oslo, Norway. Use of all or
> parts of the material requires reference to the WHO Collaborating Centre for Drug
> Statistics Methodology. **Copying and distribution for commercial purposes is not
> allowed.** Changing or manipulating the material is not allowed.

Publishing the full classification here would be redistribution, so it is left for you to
supply from a source you are licensed to use.

## What to do

Place a FHIR R4 `CodeSystem` for `http://www.whocc.no/atc` at
`fixtures/codesystem-atc.json`. `load-content.sh` picks it up automatically —
it globs `fixtures/codesystem-*.json`.

## If you skip it

Content loading and validation both still work. Any corpus resource carrying an ATC code
will report that code as unknown, which is a *provisioning* difference rather than a
validator one — exactly the confusion `TERMINOLOGY-CONTENT.md` warns about. Note it when
reading results.

`manifest.json` still lists the fixture, so `verify-content.py` will tell you it is
missing rather than letting it pass unnoticed.
