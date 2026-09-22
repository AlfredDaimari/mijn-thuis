#!/usr/bin/env bash
set -euo pipefail

# Creates a project-local virtual environment and installs Ansible only there.
# It uses the SSH agent selected by SSH_AUTH_SOCK, so load the deployment key
# first with ssh-add. It never installs packages on the control machine with apt.
# Run after setting inventory.ini; pass a playbook filename to select another
# deployment (for example ./run-playbook.sh deploy-nginx-landing.yml).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PLAYBOOK="install-docker.yml"

if [[ "${1:-}" == *.yml ]]; then
  PLAYBOOK="$1"
  shift
fi

if [[ "$PLAYBOOK" = /* || "$PLAYBOOK" == *".."* || ! -f "$SCRIPT_DIR/$PLAYBOOK" ]]; then
  echo "Playbook must be a file in $SCRIPT_DIR (for example deploy-nginx-landing.yml)." >&2
  exit 1
fi

if [[ -z "${SSH_AUTH_SOCK:-}" ]]; then
  echo "No SSH agent is available. Start one and load the VPS key with ssh-add." >&2
  exit 1
fi

if ! ssh-add -l >/dev/null 2>&1; then
  echo "No SSH key is loaded in the agent. Run: ssh-add /path/to/private_key" >&2
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR" || {
    echo "Could not create $VENV_DIR. Install Python with its venv module, then rerun." >&2
    exit 1
  }
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

# SSH authentication and passwordless sudo both use the configured VPS account;
# no password prompt or password storage is needed.
exec "$VENV_DIR/bin/ansible-playbook" -i "$SCRIPT_DIR/inventory.ini" "$SCRIPT_DIR/$PLAYBOOK" "$@"
