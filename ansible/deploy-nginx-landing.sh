#!/usr/bin/env bash
set -euo pipefail

# Configure the public Nginx edge proxy for the Docker dashboard. The shared
# runner keeps Ansible in ansible/.venv and uses the private key in ssh-agent.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/run-playbook.sh" deploy-nginx-landing.yml "$@"
