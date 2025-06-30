# src/clients/ai_client.py
import logging
from typing import List

# Per imperative, use these specific imports for Vertex AI
from google import genai
from google.genai.types import (
    GenerateContentConfig,
    GoogleSearch,
    HttpOptions,
    Tool,
)
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
        # Per imperative, instantiate a client for Vertex AI usage
        self.client = genai.Client(
            vertexai=True,
            project=config.gcp_project_id,
            location=config.vertex_ai_region,
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
                    model=f"models/{"gemini-embedding-exp-03-07"}",
                    content=batch,
                    task_type=EMBEDDING_TASK_TYPE
                )
                all_embeddings.extend(result['embedding'])
            
            logging.info(f"Successfully generated {len(all_embeddings)} embeddings.")
            return all_embeddings
        except Exception as e:
            logging.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise