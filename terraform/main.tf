terraform {
  required_version = ">= 1.5.0"

  required_providers {
    ovh = {
      source  = "ovh/ovh"
      version = "~> 2.0"
    }
  }
}

provider "ovh" {}

resource "ovh_cloud_project_ssh_key" "vm" {
  service_name = var.project_id
  region       = var.region
  name         = "${var.instance_name}-ssh"
  public_key   = file(var.ssh_public_key_path)
}

# A Discovery instance is intended for low-cost, non-production workloads.
# `d2-2` is the small profile targeted here: 2 vCPU, 4 GiB RAM, 25 GiB local
# storage, and low-bandwidth networking. Its availability varies by region.
resource "ovh_cloud_project_instance" "vm" {
  service_name = var.project_id
  region       = var.region
  name         = var.instance_name
  flavor_name  = var.flavor_name
  image_name   = var.image_name
  # The public key is registered above, then selected for this instance.
  ssh_key_name = ovh_cloud_project_ssh_key.vm.name

  # Keeps the instance on hourly billing, which is more suitable while trying
  # out a small instance. Change to true only when monthly billing is desired.
  monthly_billing = var.monthly_billing
}

output "instance_id" {
  description = "OVHcloud instance ID."
  value       = ovh_cloud_project_instance.vm.id
}

output "public_ip_addresses" {
  description = "Public IP address records assigned to the instance; use its IPv4 address in Ansible inventory."
  value       = ovh_cloud_project_instance.vm.ip_addresses
}
