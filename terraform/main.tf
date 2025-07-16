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
    "aiplatform.googleapis.com",          # Vertex AI (for Gemini models)
    "storage.googleapis.com",             # Cloud Storage
    "cloudresourcemanager.googleapis.com", # Required by many services
    "compute.googleapis.com",            # Required for creating VPC Networks
    "servicenetworking.googleapis.com",  # Required for creating VPC Networks
    "iam.googleapis.com",                 # Required for creating Service Accounts and IAM bindings
    "documentai.googleapis.com"           # NEW: Added for the new Document AI-based strategy
  ]

  # Define the predictable name for the pre-built Document AI Form Parser.
  # The processor ID is a stable value provided by Google.
  # docai_form_parser_processor_id   = "e1b714b1c73a72c1"
  # docai_form_parser_processor_id   = "8f291dd81f47f6e0"
  # docai_form_parser_processor_name = "projects/${var.project_id}/locations/eu/processors/${local.docai_form_parser_processor_id}"
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

# 1. NETWORKING: A VPC and Subnet are required for the Cloud Run Job.
resource "google_compute_network" "bsi_vpc" {
  name                    = var.vpc_network_name
  auto_create_subnetworks = false
  depends_on = [google_project_service.project_apis]
}

resource "google_compute_subnetwork" "bsi_audit_subnet" {
  name                     = "bsi-audit-subnet"
  ip_cidr_range            = "10.10.1.0/24" # A standard private IP range for the subnet
  region                   = var.region     # Must be in the same region as the Cloud Run job
  network                  = google_compute_network.bsi_vpc.id # Links it to our VPC
  private_ip_google_access = true         # Allows the job to reach Google APIs privately
  
  # Ensure the VPC network exists before creating the subnet
  depends_on = [google_compute_network.bsi_vpc]
}


# 2. IAM & PERMISSIONS: Applying the Principle of Least Privilege
# -----------------------------------------------------------------

# Grant our new Service Account permission to use Vertex AI Gemini models.
resource "google_project_iam_member" "sa_vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.bsi_job_sa.email}"
}

# Grant our new Service Account permission to use Document AI.
resource "google_project_iam_member" "sa_documentai_access" {
  project = var.project_id
  role    = "roles/documentai.apiUser"
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

# --- NEW/UPDATED: CREATE DOCUMENT AI LAYOUT PARSER PROCESSOR ---
# This creates the Layout Parser processor for layout analysis and document splitting.
resource "google_document_ai_processor" "bsi_layout_parser" {
  location     = "eu"  # Multi-region location; use "us" if preferred (both supported for this processor)
  display_name = "bsi-audit-layout-parser"
  type         = "LAYOUT_PARSER_PROCESSOR"  # Type for the Layout Parser processor

  depends_on = [google_project_service.project_apis]  # Ensure Document AI API is enabled first
}

# --- NEW: SET DEFAULT PROCESSOR VERSION ---
# This sets the default version to the specified pretrained layout parser model.
resource "google_document_ai_processor_default_version" "bsi_layout_parser_default" {
  processor = google_document_ai_processor.bsi_layout_parser.name
  version   = "${google_document_ai_processor.bsi_layout_parser.name}/processorVersions/pretrained-layout-parser-v1.0-2024-06-03"

  depends_on = [google_document_ai_processor.bsi_layout_parser]
}