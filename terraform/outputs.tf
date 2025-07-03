output "vertex_ai_index_id" {
  description = "The full resource ID of the created Vertex AI Index."
  value       = google_vertex_ai_index.bsi_audit_index.id
}

output "vertex_ai_index_endpoint_id" {
  description = "The full resource ID of the created Vertex AI Index Endpoint."
  value       = google_vertex_ai_index_endpoint.bsi_audit_endpoint.id
}

output "vertex_ai_index_endpoint_public_domain" {
  description = "The public domain name for querying the index endpoint. Our Python app will use this."
  value       = google_vertex_ai_index_endpoint.bsi_audit_endpoint.public_endpoint_domain_name
}

output "next_step_gcloud_command" {
  description = "Example gcloud command to deploy the index to the endpoint after the index is populated."
  value       = "gcloud ai index-endpoints deploy-index ${google_vertex_ai_index_endpoint.bsi_audit_endpoint.name} --index=${google_vertex_ai_index.bsi_audit_index.name} --deployed-index-id=bsi_deployed_index --display-name=bsi_deployed_index --project=${var.project_id} --region=${var.region}"
}

output "vector_index_data_gcs_path" {
  description = "The GCS path where the Python application must upload the embedding data files (e.g., index_data.jsonl). The Vertex AI Index automatically monitors this path."
  value       = local.index_contents_path
}

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