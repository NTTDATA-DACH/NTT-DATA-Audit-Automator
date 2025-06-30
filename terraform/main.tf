# Configure the Google Cloud provider
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.50.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# 1. NETWORKING: A VPC is required for the Vertex AI Index Endpoint.
# -----------------------------------------------------------------

resource "google_compute_network" "bsi_vpc" {
  name                    = var.vpc_network_name
  auto_create_subnetworks = false
}

resource "google_compute_global_address" "peering_range" {
  name          = "vertex-ai-peering-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.bsi_vpc.id
}

resource "google_service_networking_connection" "vertex_vpc_connection" {
  network                 = google_compute_network.bsi_vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.peering_range.name]
}

# 2. VECTOR DATABASE: The Vertex AI Index and its Endpoint.
# This is the core of our RAG infrastructure.
# -----------------------------------------------------------------

# The GCS path where our Python script will write the embedding files.
locals {
  index_contents_path = "gs://${var.gcs_bucket_name}/${var.customer_id}/vector_index_data/"
}

resource "google_vertex_ai_index" "bsi_audit_index" {
  # This resource takes time to create.
  display_name = "bsi-audit-index-${var.customer_id}"
  description  = "Vector search index for BSI audit documents for customer ${var.customer_id}."
  region       = var.region

  metadata {
    contents_delta_uri = local.index_contents_path
    config {
      dimensions = 768 # Dimension for Google's 'text-embedding-004' model
      approximate_neighbors_count = 150
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count = 500
        }
      }
    }
  }
  # Wait for the networking to be ready before creating the index.
  depends_on = [google_service_networking_connection.vertex_vpc_connection]
}

resource "google_vertex_ai_index_endpoint" "bsi_audit_endpoint" {
  # This resource also takes significant time to deploy.
  display_name = "bsi-audit-endpoint-${var.customer_id}"
  description  = "Endpoint for querying the BSI audit index."
  region       = var.region
  network      = google_compute_network.bsi_vpc.name
}

# NOTE: The index is NOT automatically deployed to the endpoint.
# This is a separate data-plane operation that must be done via the API or gcloud CLI
# after the index has been populated by our Python script.
# Example gcloud command:
# gcloud ai index-endpoints deploy-index INDEX_ENDPOINT_ID \
#   --index=INDEX_ID \
#   --deployed-index-id=bsi_deployed_index_${var.customer_id} \
#   --display-name=bsi_deployed_index_${var.customer_id} \
#   --project=PROJECT_ID \
#   --region=REGION