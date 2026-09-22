#!/usr/bin/env bash
set -euo pipefail

# Deploy only the public Nginx landing page. The shared runner keeps Ansible in
# ansible/.venv and uses the private key already loaded in ssh-agent.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/run-playbook.sh" deploy-nginx-landing.yml "$@"
