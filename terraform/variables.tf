variable "project_id" {
  description = "The Google Cloud Project ID where resources will be deployed."
  type        = string
}

variable "region" {
  description = "The Google Cloud region for the resources."
  type        = string
  default     = "europe-west1"
}

variable "customer_id" {
  description = "A unique identifier for the customer, used for naming resources."
  type        = string
  default     = "hisolutions"
}

variable "gcs_bucket_name" {
  description = "The name of the GCS bucket for audit data."
  type        = string
  default     = "bsi_audit_data"
}

variable "vpc_network_name" {
  description = "The name of the VPC network to create for the Vertex AI endpoint."
  type        = string
  default     = "bsi-audit-vpc"
}