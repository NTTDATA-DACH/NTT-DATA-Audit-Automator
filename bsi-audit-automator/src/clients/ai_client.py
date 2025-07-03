# src/clients/ai_client.py
import logging
import json
import asyncio
from typing import List, Dict, Any

# EXCLUSIVE USE: As directed, all interactions now use the google.cloud.aiplatform SDK.
from google.cloud import aiplatform
from google.cloud.aiplatform.models import TextEmbeddingModel, GenerativeModel

from src.config import AppConfig

# Constants for the AI client, aligned with the project brief.
GENERATIVE_MODEL_NAME = "gemini-2.5-pro"
# Using the latest, high-precision embedding model as requested.
EMBEDDING_MODEL_NAME = "text-embedding-004"
EMBEDDING_TASK_TYPE = "RETRIEVAL_DOCUMENT"

# Constants for robust generation
MAX_RETRIES = 5
MAX_CONCURRENT_REQUESTS = 10


class AiClient:
    """A client for all Vertex AI model interactions, using the aiplatform SDK."""

    def __init__(self, config: AppConfig):
        """Initializes the Vertex AI client and required models."""
        self.config = config

        # Initialize the AI Platform SDK client
        aiplatform.init(
            project=config.gcp_project_id,
            location=config.vertex_ai_region
        )

        # Instantiate specific model clients
        self.generative_model = GenerativeModel(GENERATIVE_MODEL_NAME)
        self.embedding_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

        # Semaphore to limit concurrent API calls per project brief
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        logging.info(
            f"Vertex AI Client instantiated using 'aiplatform' SDK for project "
            f"'{config.gcp_project_id}' in region '{config.vertex_ai_region}'."
        )

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generates vector embeddings for a list of text chunks.

        This implementation now uses the aiplatform.TextEmbeddingModel.

        Args:
            texts: A list of text strings to embed.

        Returns:
            A list of vector embeddings, one for each input text.
        """
        if not texts:
            logging.warning("get_embeddings called with no texts. Returning empty list.")
            return []

        logging.info(f"Requesting embeddings for {len(texts)} text chunks using model '{EMBEDDING_MODEL_NAME}'...")
        try:
            # The SDK handles batching automatically when auto_batch_size is True.
            response = self.embedding_model.get_embeddings(
                texts,
                auto_batch_size=True
            )
            # The response is a list of TextEmbedding objects; we need to extract the .values
            embeddings = [embedding.values for embedding in response]
            logging.info(f"Successfully generated {len(embeddings)} embeddings.")
            return embeddings
        except Exception as e:
            logging.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise

    async def generate_json_response(self, prompt: str, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates a JSON response from the AI model, enforcing a specific schema.
        Implements an async retry loop with exponential backoff and connection limiting.
        """
        # The generation_config is now a dictionary, which is simpler to manage.
        gen_config = {
            "response_mime_type": "application/json",
            # CRITICAL FIX: The API forbids the '$schema' key. We create a clean
            # copy of the schema without it before passing it to the model.
            "response_schema": {k: v for k, v in json_schema.items() if k != "$schema"},
            # Using the documented maximum to prevent API errors.
            "max_output_tokens": 8192,
            "temperature": 0.2,
        }

        async with self.semaphore:
            for attempt in range(MAX_RETRIES):
                try:
                    logging.info(f"Attempt {attempt + 1}/{MAX_RETRIES}: Calling Gemini model '{GENERATIVE_MODEL_NAME}'...")

                    response = await self.generative_model.generate_content_async(
                        contents=[prompt],
                        generation_config=gen_config,
                    )

                    if not response.candidates:
                        raise ValueError("The model response contained no candidates.")

                    # Explicitly check the model's finish reason per the brief's requirements.
                    finish_reason = response.candidates[0].finish_reason.name
                    if finish_reason not in ["STOP", "MAX_TOKENS"]:
                        safety_ratings_str = "N/A"
                        if response.candidates[0].safety_ratings:
                            safety_ratings_str = ", ".join([
                                f"{rating.category.name}: {rating.probability.name}"
                                for rating in response.candidates[0].safety_ratings
                            ])
                        logging.warning(
                            f"Attempt {attempt + 1} finished with non-OK reason: '{finish_reason}'. "
                            f"Safety Ratings: [{safety_ratings_str}]. Retrying..."
                        )
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue

                    # The response.text is a JSON string because we set response_mime_type.
                    response_json = json.loads(response.text)
                    logging.info(f"Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                    return response_json

                except Exception as e:
                    logging.error(f"Attempt {attempt + 1} failed with exception: {e}", exc_info=self.config.is_test_mode)
                    if attempt == MAX_RETRIES - 1:
                        logging.critical("AI generation failed after all retries.", exc_info=True)
                        raise
                    await asyncio.sleep(2 ** attempt)

        raise RuntimeError("AI generation failed after all retries without raising a final exception.")