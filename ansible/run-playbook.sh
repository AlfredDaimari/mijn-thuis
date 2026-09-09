#!/usr/bin/env bash
set -euo pipefail

# Creates a project-local virtual environment and installs Ansible only there.
# It never installs packages on the control machine with apt. Run after setting
# inventory.ini; optionally set PYTHON_BIN to choose a Python interpreter.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR" || {
    echo "Could not create $VENV_DIR. Install Python with its venv module, then rerun." >&2
    exit 1
  }
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

exec "$VENV_DIR/bin/ansible-playbook" -i "$SCRIPT_DIR/inventory.ini" "$SCRIPT_DIR/install-docker.yml" "$@"
