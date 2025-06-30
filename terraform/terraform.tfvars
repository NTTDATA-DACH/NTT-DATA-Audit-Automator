# -----------------------------------------------------------------
# Terraform Input Variables for the BSI Audit RAG Infrastructure
# -----------------------------------------------------------------
# Populate these values for your specific deployment.

# The Google Cloud project ID.
project_id = "bsi-audit-kunde-x"

# The unique identifier for the customer, used for naming resources.
customer_id = "kunde-x"

# The region where the VPC and Vertex AI resources will be deployed.
region = "europe-west1"

# The name of the GCS bucket where audit documents and index data are stored.
gcs_bucket_name = "bsi_audit_data"

# The name for the dedicated VPC network.
vpc_network_name = "bsi-audit-vpc"