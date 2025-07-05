# src/clients/ai_client.py
import logging
import json
import asyncio
import time
from typing import List, Dict, Any, Tuple

from google.cloud import aiplatform
from vertexai.language_models import TextEmbeddingModel
from google.api_core import exceptions as api_core_exceptions
from vertexai.generative_models import GenerativeModel, GenerationConfig


from src.config import AppConfig

# Constants for the AI client, aligned with the project brief.
GENERATIVE_MODEL_NAME = "gemini-2.5-pro"
EMBEDDING_MODEL_NAME = "gemini-embedding-001"

# Constants for robust generation
MAX_RETRIES = 5
EMBEDDING_BATCH_SIZE = 200


class AiClient:
    """A client for all Vertex AI model interactions, using the aiplatform SDK."""

    def __init__(self, config: AppConfig):
        """Initializes the Vertex AI client and required models."""
        self.config = config

        # Initialize the AI Platform SDK client. The 'location' parameter is the
        # correct way to specify the region for API calls.
        aiplatform.init(
            project=config.gcp_project_id,
            location=config.vertex_ai_region
        )

        # Instantiate specific model clients using the correct classes
        self.generative_model = GenerativeModel(GENERATIVE_MODEL_NAME)
        self.embedding_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

        self.semaphore = asyncio.Semaphore(config.max_concurrent_ai_requests)
        
        logging.info(
            f"Vertex AI Client instantiated for project '{config.gcp_project_id}' in region '{config.vertex_ai_region}'."
        )

    def get_embeddings(self, texts: List[str]) -> Tuple[bool, List[List[float]]]:
        """
        Generates vector embeddings for a list of text chunks in batches,
        with a robust retry mechanism per batch.

        Args:
            texts: A list of text strings to embed.

        Returns:
            A tuple containing (success: bool, embeddings: list).
        """
        if not texts:
            logging.warning("get_embeddings called with no texts. Returning empty list.")
            return True, []

        all_embeddings = []
        logging.info(f"Generating embeddings for {len(texts)} chunks in batches of {EMBEDDING_BATCH_SIZE}...")
        
        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch_texts = texts[i:i + EMBEDDING_BATCH_SIZE]
            batch_num = (i // EMBEDDING_BATCH_SIZE) + 1
            
            for attempt in range(MAX_RETRIES):
                try:
                    response = self.embedding_model.get_embeddings(batch_texts)
                    embeddings_for_batch = [embedding.values for embedding in response]
                    
                    if len(embeddings_for_batch) != len(batch_texts):
                        raise ValueError(f"API returned {len(embeddings_for_batch)} embeddings for a batch of {len(batch_texts)} texts.")

                    all_embeddings.extend(embeddings_for_batch)

                    logging.info(f"Embedding batch {batch_num} successful. Total embeddings: {len(all_embeddings)}/{len(texts)}")
                    time.sleep(0.1) # Small delay to respect rate limits
                    break  # Success, break the retry loop for this batch
                
                except api_core_exceptions.GoogleAPICallError as e:
                    wait_time = 2 ** attempt
                    if e.code == 429:
                        logging.warning(f"Embedding batch {batch_num} hit rate limit. Retrying in {wait_time}s...")
                    else:
                        logging.error(f"Embedding batch {batch_num} failed with API Error: {e}. Retrying in {wait_time}s...", exc_info=self.config.is_test_mode)
                    time.sleep(wait_time)
                
                except Exception as e:
                    wait_time = 2 ** attempt
                    logging.error(f"An unexpected error occurred in embedding batch {batch_num}: {e}. Retrying in {wait_time}s...", exc_info=True)
                    time.sleep(wait_time)

                if attempt == MAX_RETRIES - 1:
                    logging.critical(f"Embedding for batch {batch_num} failed after all retries.")
                    return False, all_embeddings # Return failure status and partial results
        
        logging.info(f"Successfully generated {len(all_embeddings)} embeddings for {len(texts)} texts.")
        return True, all_embeddings

    async def generate_json_response(self, prompt: str, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates a JSON response from the AI model, enforcing a specific schema.
        Implements an async retry loop with exponential backoff and connection limiting.
        """
        # --- BUG FIX ---
        # "Launder" the schema by serializing and deserializing it. This ensures that
        # we pass a pure, clean data structure to the Google SDK, avoiding a
        # recurring internal TypeError when it parses complex nested schemas.
        try:
            schema_for_api = json.loads(json.dumps(json_schema))
            schema_for_api.pop("$schema", None)
        except Exception as e:
            logging.error(f"Failed to process JSON schema before API call: {e}")
            raise ValueError("Invalid JSON schema provided.") from e


        gen_config = GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema_for_api,
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