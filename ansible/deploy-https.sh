#!/usr/bin/env bash
set -euo pipefail

# Issue/renew the public TLS certificate and configure the Docker dashboard
# edge proxy. The shared runner uses the SSH-agent key already loaded locally.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/run-playbook.sh" deploy-https.yml "$@"
