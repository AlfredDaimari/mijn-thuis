# Small OVHcloud Docker VM

`terraform/` defines one budget-focused OVHcloud Public Cloud instance. Its default flavor is `d2-2`, selected to target 2 vCPU, 4 GiB RAM, 25 GiB local disk, and low networking. Flavor and image labels are editable in `terraform.tfvars`; confirm that the names are offered in your chosen OVHcloud project and region before applying.

Copy `terraform/terraform.tfvars-example` to `terraform/terraform.tfvars`, fill in the project, region, image, and absolute path to your public `.pub` SSH key, then configure OVH provider credentials using its supported environment variables. Terraform registers that key in the project and attaches it to the instance. The instance resource requests the normal public network; its `public_ip_addresses` output can be used to place its IPv4 address in `ansible/inventory.ini`.

The actual `terraform.tfvars` is intentionally Git-ignored. No Terraform commands have been run by this project setup.

To install Docker, edit `ansible/inventory.ini`, make the runner executable once with `chmod +x ansible/run-playbook.sh`, and run it from the `ansible` directory. The script installs Ansible on a Debian/Ubuntu control machine if needed. The playbook always refreshes APT metadata, upgrades packages, installs `docker.io`, enables Docker, and adds the SSH user to the `docker` group.
