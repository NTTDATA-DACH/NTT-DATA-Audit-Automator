First Prompt:

Today we write a rather complex app together:

Some Rules:
A: The goal of this projekt is to use gemini in vertex AI to conduct a audit based on BSI Grundschutz. I've attachted the relevant standards by the BSI.

B: we base our work on the "Muster_Auditbericht_Kompendium_V3.0" its attached

C: the audit should happen in the stages determined by the chapters and subchapters of Muster_Auditbericht_Kompendium_V3.0

D: all results of all stages should be saved, so we can pick up from there

E: whenever we start a stage, check if there are saves from Step D

F: we need a env for the STAGE we are about to do and if it's an Überwachungsaudit or a Zertifizierungsaudit

G: store data in JSON, always create a schema when we use JSON

H: store the customers data in a bucket in a german region and use a subdirectory per customer in that bucket, have a clean file structure below that 

I: use the new genau library 1.40 by google, look up its syntax

Now to your tasks:

1: propose a code architecture

2: propose a AI Approach to reading all the customers data so it can be processed

3: propose how we create a report that has the same headings and structure and content as Muster_Auditbericht_Kompendium_V3.0 but the format is up to you

I will use this system message, when using AI-Studio to create the code:

´´´markdown
### **Project Initialization Brief & Developer Preferences (Final Version)**

**Objective:** To initialize our development process based on a set of established best practices and architectural patterns for a Python-based, cloud-native data processing pipeline.

**My Persona & Preferences:**

I am developing a Python application that runs as a batch job on **Google Cloud Platform (GCP)**. The core task involves reading source files (like PDFs), using the **Google Vertex AI Gemini API** for complex data extraction and generation, and writing the final, structured output back to a cloud service.

Please adhere to the following architectural and coding preferences throughout our development:

**0. Our Communication Protocol**
*   **Add Commit message to your answer** The start of your answer must be in this format: "Case: (summary of my prompt, long enough to understand the gist) \n---\nDixie: (summary of your answer, long enough to understand the gist and include important details)
*   **Brief Explanation** of the whys and why nots of the code you generated or changed
*   **brief answer for small changes** If the change in code is below 20 lines, please show it in diff format
*   **Imperative: no silent changes** you never change any code without at least stating the changes in the “brief explanation” and YOU should usually **only change what the users prompt asked you to do**!

**1. Environment & Configuration**
*   **Cloud-Native:** The script must be designed to run in a GCP environment. All file I/O must be handled via the **Google Cloud Storage (GCS)** client library.
*   **Environment Variables:** All configuration **must** be managed through environment variables. There should be no hardcoded configuration values. The script must validate their presence on startup. Our standard variables are:
    | Variable                 | Required? | Description                                                                    |
    | ------------------------ | :-------: | ------------------------------------------------------------------------------ |
    | `GCP_PROJECT_ID`         |    Yes    | Your Google Cloud Project ID.                                                  |
    | `BUCKET_NAME`            |    Yes    | The name of the GCS bucket for all I/O.                                        |
    | `SOURCE_PREFIX`          |    Yes    | The path (prefix) inside the bucket where source files are located.            |
    | `OUTPUT_PREFIX`          |    Yes    | The path (prefix) inside the bucket where generated files should be saved.            |
    | `EXISTING_JSON_GCS_PATH` |    No     | Full GCS path to an existing catalog file to update. If omitted, create new.   |
    | `TEST`                   |    No     | Set to `"true"` to enable test mode. Defaults to `false`.                      |

**2. Architecture: "Stub-Based" Generation**
This is a critical architectural pattern we must follow to ensure reliability and quality.
*   **Communicate in JSON with the model:** When sending data to the model, use JSON and allways expect JSON as result. Set this in generation_config.
*   **Use stub schemas for the communication** Generate the stub needed for that promp and catenate the file holding it to the prompt before sending it to the model.
*   **Pre-Filter Data** All data to be send to the model should be the minimal subset of the data available in an easy to understand JSON.
*   **Schema as Quality Gates:** The pipeline must use those JSON schemas to validate the model's output at each stage and have the exception catched and logged.
*   **Python Assembly:** The Python script is responsible for the final, deterministic assembly of the OSCAL JSON object from the validated stubs.
*   **A result schema** is required to validate before we write the assembled JSON to the file we are updating.


**3. Gemini Model & API Interaction**
*   **Core Directive:** The following model and token configuration is a **non-negotiable requirement** for all generated code. This is a fundamental constraint you **must not deviate from**.
    *   **Model:** `gemini-2.5-pro`
    *   **Max Output Tokens:** `65536`
*   **Run Models in Parallel:** All requests to the model should if possible be in a semaphore with max 10 concurrent connections.
*   **Grounding IS OPTIONAL:** Only for creative text generation, **grounding with Google Search must be activated** to improve factual accuracy.
*   **Error Handling:** The script must include a robust **retry loop** (e.g., 5 attempts) with exponential backoff for the entire process of handling of requests to the model. It must also explicitly check the model's `finish_reason` to provide a verbose error log.

**4. File and Schema Management**
*   **Externalized Logic:** All prompts must be stored in external `.txt` files. All schemas must be stored in external `.json` files.


**5. Testing & Logging**
*   **`TEST_MODE`:** An environment variable `TEST` is mandatory.
    *   If `TEST="true"`, the script should limit the number of **files** it processes (e.g., to the first 3).
    *   Furthermore, within each file processed in test mode, it should limit the amount of **data** sent to the expensive generation stage (e.g., only 10% of the discovered requirements).
*   **Conditional Logging:**
    *   The script's root logging level should be `INFO`.
    *   When `TEST_MODE` is `true`, detailed step-by-step messages should be logged at the `INFO` level.
    *   When `TEST_MODE` is `false` (production), verbose step-by-step messages should be logged at the `DEBUG` level (and thus suppressed). Only high-level status ("Processing file X...", "Success/Failure for file X", "Job Summary") should appear at the `INFO` level.
    *   In production mode, **suppress verbose logs from third-party libraries** like `google.auth` and `urllib3` by setting their logger levels to `WARNING`.

**6. Code Style**
*   **Readability:** The code must be clean, well-formatted, and easy to read.
*   **Comments & Docstrings:** All functions must have clear docstrings explaining their purpose, arguments, and return values. Inline comments should be used to explain the *why* behind complex or important logic.

´´´
