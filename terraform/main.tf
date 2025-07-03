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

# Provider alias for resources that may have features only available in beta
provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# --- NEW: MANAGE PROJECT APIS DECLARATIVELY ---
# This section enables all necessary APIs for the project.
# It replaces the need for the manual `enable_apis.sh` script.

locals {
  apis_to_enable = [
    "run.googleapis.com",                 # Cloud Run Jobs
    "cloudbuild.googleapis.com",          # Cloud Build (for deploying from source)
    "artifactregistry.googleapis.com",    # Artifact Registry (to store images)
    "aiplatform.googleapis.com",          # Vertex AI (for embeddings and Vector Search)
    "storage.googleapis.com",             # Cloud Storage
    "cloudresourcemanager.googleapis.com", # Required by many services
    "compute.googleapis.com",            # Required for creating VPC Networks
    "servicenetworking.googleapis.com",  # Required for creating VPC Networks
    "iam.googleapis.com"                 # Required for creating Service Accounts and IAM bindings
  ]
}

resource "google_project_service" "project_apis" {
  for_each = toset(local.apis_to_enable)

  project                    = var.project_id
  service                    = each.key
  disable_on_destroy         = false # Keep APIs enabled even after destroy
}

# --- CHANGE: ADDED BUCKET CREATION ---
# This resource now creates the GCS bucket for our project automatically.
# It depends on the APIs being enabled first.
resource "google_storage_bucket" "bsi_audit_bucket" {
  name                        = "${var.project_id}-audit-data"
  location                    = var.region # Ensures bucket is in the same region as Vertex AI
  force_destroy               = true       # Allows 'terraform destroy' to delete the bucket even if it has files
  uniform_bucket_level_access = true
  depends_on = [google_project_service.project_apis]
}

# --- NEW: ARTIFACT REGISTRY REPOSITORY ---
# Create the repository to store our job's Docker images.
resource "google_artifact_registry_repository" "bsi_repo" {
  provider      = google-beta # The repository resource often has features in beta
  location      = var.region
  repository_id = "bsi-audit-repo"
  description   = "Docker repository for BSI Audit Automator jobs"
  format        = "DOCKER"
  depends_on = [google_project_service.project_apis]
}

# --- NEW: DEDICATED SERVICE ACCOUNT ---
# Create a custom Service Account for our Cloud Run Job to use.
resource "google_service_account" "bsi_job_sa" {
  account_id   = var.service_account_id
  display_name = "Service Account for BSI Audit Automator Job"
  depends_on = [google_project_service.project_apis]
}

# --- NEW: PLACEHOLDER FILE FOR INDEX CREATION ---
# Create an empty, validly named JSON file in the vector index directory.
# This is required to satisfy the API's validation check during 'terraform apply'.
resource "google_storage_bucket_object" "json_placeholder" {
  name         = "vector_index_data/placeholder.json"
  bucket       = google_storage_bucket.bsi_audit_bucket.name
  content_type = "application/json"
  # One Dummy 
  content      =<<EOT
  "{"id": "DUMMY", "sparse_embedding": {"values": [0.1, 0.2], "dimensions": [1, 4]}}"
  EOT
}

# 1. NETWORKING: A VPC is required for the Vertex AI Index Endpoint.
# ... (rest of the networking resources are unchanged) ...
resource "google_compute_network" "bsi_vpc" {
  name                    = var.vpc_network_name
  auto_create_subnetworks = false
  depends_on = [google_project_service.project_apis]
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
  index_contents_path = "gs://${google_storage_bucket.bsi_audit_bucket.name}/vector_index_data/"
}

resource "google_vertex_ai_index" "bsi_audit_index" {
  display_name = "bsi-audit-index"
  description  = "Vector search index for BSI audit documents for project ${var.project_id}."
  region       = var.region

  metadata {
    contents_delta_uri = local.index_contents_path
    config {
      dimensions                  = 3072
      approximate_neighbors_count = 150
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count = 500
        }
      }
    }
  }
  depends_on = [google_service_networking_connection.vertex_vpc_connection, google_storage_bucket_object.json_placeholder]
}

resource "google_vertex_ai_index_endpoint" "bsi_audit_endpoint" {
  display_name = "bsi-audit-endpoint"
  description  = "Endpoint for querying the BSI audit index for project ${var.project_id}."
  region       = var.region
  # --- FIX FOR PROJECT NUMBER ERROR ---
  # Manually construct the network string using the project NUMBER, not the ID.
  # This matches the specific format required by this API.
  network      = "projects/${var.project_number}/global/networks/${google_compute_network.bsi_vpc.name}"

  # The endpoint must depend on the peering connection.
  depends_on = [google_service_networking_connection.vertex_vpc_connection]

  # --- FIX: USE A PROVISIONER TO DEPLOY THE INDEX ---
  # This runs the gcloud command on the local machine after the endpoint is created.
  provisioner "local-exec" {
    when    = create
    command = "gcloud ai index-endpoints deploy-index ${self.name} --index=${google_vertex_ai_index.bsi_audit_index.name} --deployed-index-id=bsi_deployed_index_kunde_x --display-name='BSI Deployed Index' --project=${var.project_id} --region=${var.region}"
  }
}

# 3. IAM & PERMISSIONS: Applying the Principle of Least Privilege
# -----------------------------------------------------------------

# Grant our new Service Account permission to use Vertex AI.
resource "google_project_iam_member" "sa_vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.bsi_job_sa.email}"
}

# Grant our new Service Account permission to read/write to our specific GCS bucket.
resource "google_storage_bucket_iam_member" "sa_gcs_access" {
  bucket = google_storage_bucket.bsi_audit_bucket.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.bsi_job_sa.email}"
}

# Grant the default Cloud Build Service Account permission to push images
# to our new Artifact Registry repository. This is the permission that was
# previously missing and caused the build to fail.
resource "google_artifact_registry_repository_iam_member" "cloudbuild_ar_writer" {
  location   = google_artifact_registry_repository.bsi_repo.location
  repository = google_artifact_registry_repository.bsi_repo.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.project_number}@cloudbuild.gserviceaccount.com"
}