# src/clients/ai_client.py
import logging
from typing import List
import google.generativeai as genai
from src.config import AppConfig

# Constants for the AI client
EMBEDDING_MODEL_NAME = "text-embedding-004"
EMBEDDING_TASK_TYPE = "RETRIEVAL_DOCUMENT"
EMBEDDING_BATCH_SIZE = 100  # API limit for text-embedding-004 is 100

class AiClient:
    """A client for all Vertex AI model interactions."""

    def __init__(self, config: AppConfig):
        """Initializes the Vertex AI client."""
        self.config = config
        genai.configure(
            project=config.gcp_project_id,
            location=config.vertex_ai_region,
        )
        logging.info(f"Vertex AI Client configured for project '{config.gcp_project_id}' in region '{config.vertex_ai_region}'.")

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generates vector embeddings for a list of text chunks.

        Args:
            texts: A list of text strings to embed.

        Returns:
            A list of vector embeddings, one for each input text.
        """
        logging.info(f"Requesting embeddings for {len(texts)} text chunks using model '{EMBEDDING_MODEL_NAME}'.")
        all_embeddings = []
        try:
            for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch = texts[i:i + EMBEDDING_BATCH_SIZE]
                logging.debug(f"Processing batch {i//EMBEDDING_BATCH_SIZE + 1}...")
                # The genai library handles the direct API call
                result = genai.embed_content(
                    model=f"models/{EMBEDDING_MODEL_NAME}",
                    content=batch,
                    task_type=EMBEDDING_TASK_TYPE
                )
                all_embeddings.extend(result['embedding'])
            
            logging.info(f"Successfully generated {len(all_embeddings)} embeddings.")
            return all_embeddings
        except Exception as e:
            logging.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise