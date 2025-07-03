# src/clients/ai_client.py
import logging
import json
import asyncio
import time
from typing import List, Dict, Any, Tuple

from google.cloud import aiplatform
from google.api_core import client_options
from vertexai.language_models import TextEmbeddingModel
from google.api_core import exceptions as api_core_exceptions
from vertexai.generative_models import GenerativeModel, GenerationConfig

from src.config import AppConfig

# Constants for the AI client, aligned with the project brief.
GENERATIVE_MODEL_NAME = "gemini-2.5-pro"
EMBEDDING_MODEL_NAME = "gemini-embedding-001"

# Constants for robust generation
MAX_RETRIES = 5


class AiClient:
    """A client for all Vertex AI model interactions, using the aiplatform SDK."""

    def __init__(self, config: AppConfig):
        """Initializes the Vertex AI client and required models."""
        self.config = config

        # --- CRITICAL FIX for regional endpoints ---
        # Construct the regional API endpoint URL to guarantee requests are sent
        # to the specified region, not a global or us-central1 default.
        api_endpoint = f"{config.vertex_ai_region}-aiplatform.googleapis.com"
        client_opts = client_options.ClientOptions(api_endpoint=api_endpoint)

        # Initialize the AI Platform SDK client with the explicit endpoint
        aiplatform.init(
            project=config.gcp_project_id,
            location=config.vertex_ai_region,
            client_options=client_opts
        )

        # Instantiate specific model clients using the correct classes
        self.generative_model = GenerativeModel(GENERATIVE_MODEL_NAME)
        self.embedding_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

        # Semaphore to limit concurrent API calls per project brief
        self.semaphore = asyncio.Semaphore(config.max_concurrent_ai_requests)
        logging.info(
            f"Vertex AI Client instantiated for project '{config.gcp_project_id}' "
            f"and forced to regional endpoint '{api_endpoint}'."
        )

    def get_embeddings(self, texts: List[str]) -> Tuple[bool, List[List[float]]]:
        """
        Generates vector embeddings for a list of text chunks. Implements a
        robust retry mechanism.

        Args:
            texts: A list of text strings to embed.

        Returns:
            A tuple containing (success: bool, embeddings: list).
        """
        if not texts:
            logging.warning("get_embeddings called with no texts. Returning empty list.")
            return True, []

        all_embeddings = []
        logging.info(f"Generating embeddings for {len(texts)} chunks...")
        
        # We must iterate and call the API for each text individually with its own retry logic.
        for i, text in enumerate(texts):
            for attempt in range(MAX_RETRIES):
                try:
                    # The model expects a list, even if it's a single item.
                    response = self.embedding_model.get_embeddings([text])
                    # The response is a list with one TextEmbedding object.
                    all_embeddings.append(response[0].values)
                    logging.debug(f"Generated embedding for chunk {i+1}/{len(texts)}")
                    break  # Success, break the retry loop for this chunk
                except api_core_exceptions.GoogleAPICallError as e:
                    if e.code == 429:  # HTTP status for "Too Many Requests"
                        wait_time = 2 ** attempt
                        logging.warning(f"Embedding for chunk {i+1} hit rate limit. Retrying in {wait_time}s...")
                        time.sleep(wait_time) # Use synchronous sleep
                    else:
                        logging.error(f"Embedding for chunk {i+1} failed with API Error: {e}", exc_info=True)
                        raise # Re-raise other API errors immediately
                
                if attempt == MAX_RETRIES - 1:
                    logging.critical(f"Embedding for chunk {i+1} failed after all retries.")
                    return False, all_embeddings # Return failure status and partial results
        
        logging.info(f"Successfully generated {len(all_embeddings)} embeddings.")
        return True, all_embeddings

    async def generate_json_response(self, prompt: str, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates a JSON response from the AI model, enforcing a specific schema.
        Implements an async retry loop with exponential backoff and connection limiting.
        """
        gen_config = GenerationConfig(
            response_mime_type="application/json",
            response_schema={k: v for k, v in json_schema.items() if k != "$schema"},
            max_output_tokens=8192,
            temperature=0.2,
        )

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
                        await asyncio.sleep(2 ** attempt)
                        continue

                    response_json = json.loads(response.text)
                    logging.info(f"Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                    return response_json

                except api_core_exceptions.GoogleAPICallError as e:
                    if e.code == 429:
                        logging.warning(
                            f"Attempt {attempt + 1} hit rate limit (429). "
                            f"Retrying in {2 ** attempt}s..."
                        )
                    else:
                        logging.error(
                            f"Attempt {attempt + 1} failed with Google API Error (Code: {e.code}). Retrying...",
                            exc_info=self.config.is_test_mode
                        )
                except Exception as e:
                    logging.error(f"Attempt {attempt + 1} failed with a non-API exception. Retrying...", exc_info=True)

                if attempt == MAX_RETRIES - 1:
                    logging.critical("AI generation failed after all retries.", exc_info=True)
                    raise
                
                await asyncio.sleep(2 ** attempt)
        
        raise RuntimeError("AI generation failed after all retries without raising a final exception.")