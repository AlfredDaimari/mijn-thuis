variable "project_id" {
  description = "OVHcloud Public Cloud project/service name."
  type        = string
}

variable "instance_name" {
  description = "Name displayed for this one virtual machine."
  type        = string
}

variable "region" {
  description = "OVHcloud Public Cloud region in which to create the VM."
  type        = string
}

variable "flavor_name" {
  description = "Instance flavor. d2-2 targets 2 vCPU, 4 GiB memory, 25 GiB local storage, and low networking."
  type        = string
  default     = "d2-2"
}

variable "image_name" {
  description = "Boot image name available in the selected OVHcloud region, for example Ubuntu 24.04."
  type        = string
}

variable "ssh_public_key_path" {
  description = "Absolute or Terraform-relative path to the public SSH key (.pub) to register for the VM."
  type        = string
}

variable "monthly_billing" {
  description = "Whether to request monthly rather than hourly billing."
  type        = bool
  default     = false
}
