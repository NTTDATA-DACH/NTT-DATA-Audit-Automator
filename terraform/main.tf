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

# --- CHANGE: ADDED BUCKET CREATION ---
# This resource now creates the GCS bucket for our project automatically.
resource "google_storage_bucket" "bsi_audit_bucket" {
  name                        = "${var.project_id}-${var.customer_id}-audit-data"
  location                    = var.region # Ensures bucket is in the same region as Vertex AI
  force_destroy               = true       # Allows 'terraform destroy' to delete the bucket even if it has files
  uniform_bucket_level_access = true
}

# 1. NETWORKING: A VPC is required for the Vertex AI Index Endpoint.
# ... (rest of the networking resources are unchanged) ...
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
# -----------------------------------------------------------------

locals {
  # --- CHANGE: DYNAMICALLY USE THE CREATED BUCKET ---
  # This path now refers to the bucket created above, not a variable.
  index_contents_path = "gs://${google_storage_bucket.bsi_audit_bucket.name}/${var.customer_id}/vector_index_data/"
}

resource "google_vertex_ai_index" "bsi_audit_index" {
  display_name = "bsi-audit-index-${var.customer_id}"
  description  = "Vector search index for BSI audit documents for customer ${var.customer_id}."
  region       = var.region

  metadata {
    contents_delta_uri = local.index_contents_path
    config {
      dimensions                  = 768
      approximate_neighbors_count = 150
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count = 500
        }
      }
    }
  }
  depends_on = [google_service_networking_connection.vertex_vpc_connection]
}

resource "google_vertex_ai_index_endpoint" "bsi_audit_endpoint" {
  display_name = "bsi-audit-endpoint-${var.customer_id}"
  description  = "Endpoint for querying the BSI audit index."
  region       = var.region
  # --- FIX FOR PROJECT NUMBER ERROR ---
  # Manually construct the network string using the project NUMBER, not the ID.
  # This matches the specific format required by this API.
  network      = "projects/${var.project_number}/global/networks/${google_compute_network.bsi_vpc.name}"
}