variable "tenancy_ocid" {
  type        = string
  description = "Tenancy OCID, used to look up availability domains."
}

variable "compartment_ocid" {
  type        = string
  description = "Compartment that owns the network and the cluster."
}

variable "region" {
  type        = string
  description = "OCI region, for example us-ashburn-1."
}

variable "name" {
  type        = string
  default     = "llm-inference"
  description = "Name prefix for all resources."
}

variable "kubernetes_version" {
  type        = string
  description = "OKE Kubernetes version, for example v1.31.1."
}

variable "api_allowed_cidr" {
  type        = string
  description = "CIDR allowed to reach the Kubernetes API endpoint. Use your own /32."
}

variable "cpu_node_image_ocid" {
  type        = string
  description = "OKE worker image OCID for the system pool."
}

variable "gpu_node_image_ocid" {
  type        = string
  description = "OKE GPU worker image OCID (ships with NVIDIA drivers)."
}

variable "gpu_shape" {
  type        = string
  default     = "VM.GPU.A10.1"
  description = "GPU shape for the inference pool."
}

variable "gpu_node_count" {
  type        = number
  default     = 1
  description = "Number of GPU worker nodes."
}
