#!/usr/bin/env bash
set -euo pipefail

# Installs Ansible on a Debian/Ubuntu control machine, then provisions the VM.
# Run this from the ansible directory after setting inventory.ini.
if ! command -v ansible-playbook >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ansible
fi

ansible-playbook -i inventory.ini install-docker.yml "$@"
