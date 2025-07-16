from google.cloud import documentai
from google.api_core.client_options import ClientOptions

location = "us"  # or your region, e.g., "eu"
opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
client = documentai.DocumentProcessorServiceClient(client_options=opts)
print("Client initialized successfully!")
