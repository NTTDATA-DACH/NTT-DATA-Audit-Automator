data "google_project" "project" {
  # This data source uses the project ID configured in the Google provider
  # to fetch details about the project, such as its number.
  # The provider is configured via the `project_id` in your .tfvars file.
}