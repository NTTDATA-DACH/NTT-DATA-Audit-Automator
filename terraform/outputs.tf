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

# --- FIX FOR INVALID_ARGUMENT ERROR ---
# The 'deployed-index-id' has strict naming rules (letters, numbers, underscores only).
# This output now generates a valid ID string by using underscores instead of hyphens.
output "next_step_gcloud_command" {
  description = "Example gcloud command to deploy the index to the endpoint after the index is populated."
  value       = "gcloud ai index-endpoints deploy-index ${google_vertex_ai_index_endpoint.bsi_audit_endpoint.name} --index=${google_vertex_ai_index.bsi_audit_index.name} --deployed-index-id=bsi_deployed_index_${replace(var.customer_id, "-", "_")} --display-name=bsi_deployed_index_${replace(var.customer_id, "-", "_")} --project=${var.project_id} --region=${var.region}"
}