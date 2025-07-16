# bsi-audit-automator/crop_test_pdf.py
import os
import fitz  # PyMuPDF
from google.cloud import storage
from dotenv import load_dotenv

def create_test_pdf():
    """
    Downloads a large PDF from GCS, crops it to the first 40 pages,
    and uploads the result as 'test.pdf' to the source_documents prefix.
    """
    # Load environment variables from .env file and a potential envs.sh source
    load_dotenv()
    
    bucket_name = os.getenv("BUCKET_NAME")
    project_id = os.getenv("GCP_PROJECT_ID")
    source_prefix = os.getenv("SOURCE_PREFIX", "source_documents/")

    if not bucket_name or not project_id:
        print("❌ Error: BUCKET_NAME and GCP_PROJECT_ID must be set.")
        print("   Please run 'source ./envs.sh' first.")
        return

    source_blob_name = "A.4_Grundschutz-Check_HiSolutions_AG_2025-06-20.pdf"
    destination_blob_name = f"{source_prefix}A.4_Grundschutz-Check_HiSolutions_AG_test.pdf"
    pages_to_keep = 250

    print(f"🔹 Initializing GCS client for project '{project_id}'...")
    try:
        storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)

        print(f"🔹 Downloading source file: gs://{bucket_name}/{source_blob_name}")
        source_blob = bucket.blob(source_blob_name)
        pdf_bytes = source_blob.download_as_bytes()

        print(f"🔹 Cropping PDF to the first {pages_to_keep} pages...")
        source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        if len(source_doc) < pages_to_keep:
            print(f"⚠️ Warning: Source PDF has only {len(source_doc)} pages. Keeping all of them.")
            pages_to_keep = len(source_doc)

        new_doc = fitz.open()  # Create a new, empty PDF
        new_doc.insert_pdf(source_doc, from_page=0, to_page=pages_to_keep - 1)

        print(f"🔹 Uploading new file to: gs://{bucket_name}/{destination_blob_name}")
        destination_blob = bucket.blob(destination_blob_name)
        destination_blob.upload_from_string(
            new_doc.write(),
            content_type="application/pdf"
        )

        print(f"✅ Successfully created '{destination_blob_name}'.")

    except Exception as e:
        print(f"❌ An error occurred: {e}")
        print("   Ensure you are authenticated ('gcloud auth application-default login')")
        print(f"   and that the source file 'gs://{bucket_name}/{source_blob_name}' exists.")

if __name__ == "__main__":
    create_test_pdf()