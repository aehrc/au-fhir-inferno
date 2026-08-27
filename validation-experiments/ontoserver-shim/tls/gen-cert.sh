#!/bin/bash
# Generate the self-signed cert the TLS terminator uses.
#
# DESIGN: generated, never committed. The wrapper insists on an https terminology server,
# so a local rig needs TLS in front of Ontoserver -- but a private key in a public repo is
# both bad practice and a magnet for secret scanners, and this one is worthless outside
# the compose network anyway.
#
# CONSTRAINT: the SAN must carry BOTH names. `tx-tls` is how the wrapper container reaches
# it across the compose network; `localhost` is how anything on the host does. A cert with
# only one of them fails hostname verification for the other caller.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f tx-tls.crt ] && [ -f tx-tls.key ]; then
  echo "tx-tls.crt / tx-tls.key already present -- delete them to regenerate"
  exit 0
fi

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout tx-tls.key -out tx-tls.crt \
  -subj "/CN=tx-tls" \
  -addext "subjectAltName=DNS:tx-tls,DNS:localhost" 2>/dev/null

chmod 600 tx-tls.key
echo "wrote tls/tx-tls.crt and tls/tx-tls.key"
openssl x509 -in tx-tls.crt -noout -subject -ext subjectAltName
