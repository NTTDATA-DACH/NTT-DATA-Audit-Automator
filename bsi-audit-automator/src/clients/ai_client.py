# src/clients/ai_client.py
import logging
import json
from typing import List

# Per imperative, use these specific imports for Vertex AI
from google import genai
from google.genai import types
from google.genai.types import (

    
    GenerateContentConfig,
    GoogleSearch,
    HttpOptions,
    Tool,
)
from src.config import AppConfig

# Constants for the AI client
EMBEDDING_MODEL_NAME = "gemini-embedding-001"
# EMBEDDING_MODEL_NAME = "text-embedding-005"
EMBEDDING_TASK_TYPE = "RETRIEVAL_DOCUMENT"
EMBEDDING_BATCH_SIZE = 1  # gemini-embedding supports 1, ; set 250 for text-embedding-005

class AiClient:
    """A client for all Vertex AI model interactions."""

    def __init__(self, config: AppConfig):
        """Initializes the Vertex AI client."""
        self.config = config
        # Per imperative, instantiate a client for Vertex AI usage
        self.client = genai.Client(
            vertexai=True,
            project=config.gcp_project_id,
            #location="global",  
            location=config.vertex_ai_region
            #http_options=types.HttpOptions(api_version='v1')
        )
        logging.info(f"Vertex AI Client instantiated for project '{config.gcp_project_id}' in region '{config.vertex_ai_region}'.")

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generates vector embeddings for a list of text chunks.
        Args:
            texts: A list of text strings to embed.
        Returns:
            A list of vector embeddings, one for each input text.
        """
        if not texts:
            logging.warning("get_embeddings called with no texts. Returning empty list.")
            return []
        
        logging.info(f"Requesting embeddings for {len(texts)} text chunks using model '{EMBEDDING_MODEL_NAME}'...")
        all_embeddings = []
        try:
            for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch = texts[i:i + EMBEDDING_BATCH_SIZE]
                logging.debug(f"Processing batch {i//EMBEDDING_BATCH_SIZE + 1}...")
                # The client is configured once and used implicitly by the top-level functions.
                result = self.client.models.embed_content(
                    # model=f"models/{EMBEDDING_MODEL_NAME}",
                    model=EMBEDDING_MODEL_NAME,     # <-- NO “models/” prefix
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=EMBEDDING_TASK_TYPE,
                        output_dimensionality=768
                        )
                )
                # all_embeddings.extend(result['embedding'])
                all_embeddings.append(result.embeddings[0].values)
            
            logging.info(f"Successfully generated {len(all_embeddings)} embeddings.")
            return all_embeddings
        except Exception as e:
            logging.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise

    async def generate_json_response(self, prompt: str, json_schema: dict) -> dict:
        """
        Generates a JSON response from the AI model, enforcing a specific schema.
        This is a placeholder for the full async, retry, and validation logic.

        Args:
            prompt: The full text prompt for the model.
            json_schema: A dictionary representing the JSON schema for the output.

        Returns:
            A dictionary with the validated data from the model.
        """
        logging.info("AI Client: Generating JSON response (placeholder implementation).")

        # In a real implementation, this would use self.client.aio.models.generate_content
        # with JSON mode enabled and would perform validation.
        
        # --- Start of Placeholder Logic ---
        dummy_response = {}
        props = json_schema.get("properties", {})

        # Check if the schema expects a list of rows (like in Chapter 4)
        if "rows" in props and props["rows"].get("type") == "array":
            dummy_response["rows"] = [
                {
                    "Schicht": "ORP",
                    "Baustein": "ORP.4 Identitäts- und Berechtigungsmanagement",
                    "Zielobjekt": "Active Directory",
                    "Begruendung zur Auswahl": "Kritische Komponente für den Zugriff auf alle Systeme."
                },
                {
                    "Schicht": "INF",
                    "Baustein": "INF.2 Clients unter Windows",
                    "Zielobjekt": "Standard-Mitarbeiter-Notebooks",
                    "Begruendung zur Auswahl": "Hohe Angriffsfläche und Verbreitung in der Organisation."
                }
            ]
        # Check if the schema expects answers and a finding (like in Chapter 3)
        elif "answers" in props and "findingText" in props:
            dummy_response["answers"] = []
            dummy_response["findingText"] = "Alle Dokumente wurden geprüft und für angemessen befunden. Es wurden keine Abweichungen festgestellt."
            answer_items = props["answers"].get("items", [])
            for item in answer_items:
                if item.get("type") == "boolean":
                    dummy_response["answers"].append(True)
                elif item.get("format") == "date":
                    dummy_response["answers"].append("2024-01-01")
                else:
                    dummy_response["answers"].append("Placeholder")
        
        logging.info("AI Client: Successfully generated and validated dummy JSON response.")
        return dummy_response
        # --- End of Placeholder Logic ---