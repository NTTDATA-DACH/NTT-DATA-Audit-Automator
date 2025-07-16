output "service_account_email" {
  description = "The email of the custom service account created for the Cloud Run Job."
  value       = google_service_account.bsi_job_sa.email
}

output "artifact_registry_repository_url" {
  description = "The URL of the created Artifact Registry repository."
  value       = "${google_artifact_registry_repository.bsi_repo.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.bsi_repo.repository_id}"
}

output "region" {
  description = "The Google Cloud region where resources are deployed."
  value       = var.region
}

output "artifact_registry_repository_name" {
  description = "The name (repository ID) of the created Artifact Registry repository."
  value       = google_artifact_registry_repository.bsi_repo.repository_id
}

output "project_id" {
  description = "The Google Cloud project ID where resources are deployed."
  value       = var.project_id
}

output "project_number" {
  description = "The Google Cloud project number where resources are deployed."
  value       = data.google_project.project.number
}

output "vpc_network_name" {
  description = "The name of the VPC network created for the audit resources."
  value       = google_compute_network.bsi_vpc.name
}

output "subnet_name" {
  description = "The name of the Subnet created for the Cloud Run Job to connect to."
  value       = google_compute_subnetwork.bsi_audit_subnet.name
}

output "gcs_bucket_name" {
    description = "The name of the GCS bucket created for the audit data."
    value       = google_storage_bucket.bsi_audit_bucket.name
}