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

# Orders one OVHcloud VPS, rather than a Public Cloud instance. The VPS product
# includes its normal public network access; no Public Cloud project, network,
# or SSH-key resource is required.
resource "ovh_vps" "vm" {
  display_name   = var.instance_name
  ovh_subsidiary = var.ovh_subsidiary

  # OVH requires image_id when a public SSH key is supplied during creation.
  image_id       = var.image_id
  public_ssh_key = file(var.ssh_public_key_path)

  plan = [{
    duration     = "P1M"
    plan_code    = var.vps_plan_code
    pricing_mode = "default"

    configuration = [
      {
        label = "vps_datacenter"
        value = var.datacenter
      },
      {
        label = "vps_os"
        value = var.os_name
      }
    ]
  }]
}

output "vps_service_name" {
  description = "OVHcloud VPS service name. Use the assigned public IPv4 from the OVHcloud Control Panel in Ansible inventory."
  value       = ovh_vps.vm.name
}

output "vps_display_name" {
  description = "Configured VPS display name."
  value       = ovh_vps.vm.display_name
}
