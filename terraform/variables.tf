variable "project_id" {
  description = "The Google Cloud Project ID where resources will be deployed."
  type        = string
}

variable "project_number" {
  description = "The unique numeric identifier for the Google Cloud project."
  type        = string
}

variable "region" {
  description = "The Google Cloud region for the resources. Must support Vertex AI Vector Search."
  type        = string
  default     = "europe-west4" # A region that supports the service
}

variable "customer_id" {
  description = "A unique identifier for the customer, used for naming resources."
  type        = string
  default     = "hisolutions"
}

variable "vpc_network_name" {
  description = "The name of the VPC network to create for the Vertex AI endpoint."
  type        = string
  default     = "bsi-audit-vpc"
}

variable "service_account_id" {
  description = "The ID for the custom service account (e.g., 'bsi-automator-sa')."
  type        = string
}