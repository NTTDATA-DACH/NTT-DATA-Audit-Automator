#!/bin/bash
# A script to create the BSI Audit Automator project structure.

echo "Creating project structure for bsi-audit-automator..."

# Create the root directory and navigate into it
mkdir -p bsi-audit-automator
cd bsi-audit-automator

# Create top-level files and directories
touch main.py requirements.txt
mkdir -p src tests assets

# Create src subdirectories
mkdir -p src/clients src/audit src/audit/stages

# Create files in src
touch src/__init__.py
touch src/config.py
touch src/logging_setup.py

# Create files in src/clients
touch src/clients/__init__.py
touch src/clients/gcs_client.py
touch src/clients/ai_client.py

# Create files in src/audit and src/audit/stages
touch src/audit/__init__.py
touch src/audit/controller.py
touch src/audit/report_generator.py
touch src/audit/stages/__init__.py
touch src/audit/stages/stage_1_general.py
touch src/audit/stages/stage_3_document_review.py

# Create assets subdirectories and placeholder files
mkdir -p assets/prompts assets/schemas
touch assets/prompts/initial_extraction.txt
touch assets/prompts/stage_3_1_actuality.txt
touch assets/schemas/initial_extraction_schema.json
touch assets/schemas/stage_3_1_actuality_schema.json

# Create placeholder test file
touch tests/test_placeholder.py

echo "Project structure created successfully in the 'bsi-audit-automator' directory."