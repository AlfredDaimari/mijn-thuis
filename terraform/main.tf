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

# This configuration deliberately reads an already-paid-for VPS. It has no
# resources, so it cannot order, reinstall, resize, or otherwise change a VPS.
# The service name is visible in OVHcloud Manager under Bare Metal Cloud >
# Virtual Private Servers > your VPS.
data "ovh_vps" "existing" {
  service_name = var.vps_service_name
}

output "vps_service_name" {
  description = "OVHcloud's internal service name for the existing VPS."
  value       = data.ovh_vps.existing.id
}

output "vps_display_name" {
  description = "Display name configured for the existing VPS."
  value       = data.ovh_vps.existing.displayname
}

output "vps_datacenter" {
  description = "Datacenter reported by OVHcloud for the existing VPS."
  value       = data.ovh_vps.existing.datacenter
}

output "vps_public_ips" {
  description = "IP addresses attached to the existing VPS; copy its IPv4 to ansible/inventory.ini."
  value       = data.ovh_vps.existing.ips
}

output "vps_resources" {
  description = "Compute capacity reported by OVHcloud for the existing VPS."
  value = {
    memory_mb = data.ovh_vps.existing.memory
    vcores    = data.ovh_vps.existing.vcore
  }
}
