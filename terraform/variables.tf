variable "instance_name" {
  description = "Display name for this one VPS."
  type        = string
}

variable "ovh_subsidiary" {
  description = "OVHcloud subsidiary that bills the order, for example NL."
  type        = string
}

variable "vps_plan_code" {
  description = "Current OVHcloud VPS-1 plan code for the selected subsidiary."
  type        = string
}

variable "datacenter" {
  description = "OVHcloud VPS datacenter code offered for the chosen plan."
  type        = string
}

variable "os_name" {
  description = "Operating-system label offered by OVHcloud for this VPS plan, for example Ubuntu 24.04."
  type        = string
}

variable "image_id" {
  description = "OVHcloud image ID for the chosen operating system. Required when pre-installing a public SSH key."
  type        = string
}

variable "ssh_public_key_path" {
  description = "Absolute or Terraform-relative path to the public SSH key (.pub) to pre-install on the VPS."
  type        = string
}
