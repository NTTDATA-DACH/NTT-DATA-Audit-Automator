# src/clients/ai_client.py
import logging
import json
import asyncio
from typing import List, Dict, Any, Tuple

from google.cloud import aiplatform
from vertexai.language_models import TextEmbeddingModel
from google.api_core import exceptions as api_core_exceptions
from vertexai.generative_models import GenerativeModel, GenerationConfig

from src.config import AppConfig

# Constants for the AI client, aligned with the project brief.
GENERATIVE_MODEL_NAME = "gemini-2.5-pro"
EMBEDDING_MODEL_NAME = "gemini-embedding-001"
MAX_RETRIES = 5
# The Gemini embedding model has a batch size limit.
EMBEDDING_BATCH_SIZE = 250


class AiClient:
    """A client for all Vertex AI model interactions, using the aiplatform SDK."""

    def __init__(self, config: AppConfig):
        """Initializes the Vertex AI client and required models."""
        self.config = config
        aiplatform.init(project=config.gcp_project_id, location=config.vertex_ai_region)
        self.generative_model = GenerativeModel(GENERATIVE_MODEL_NAME)
        self.embedding_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)
        self.semaphore = asyncio.Semaphore(config.max_concurrent_ai_requests)
        logging.info(f"Vertex AI Client instantiated for project '{config.gcp_project_id}' in region '{config.vertex_ai_region}'.")

    async def get_embeddings(self, texts: List[str]) -> Tuple[bool, List[List[float]]]:
        """
        Generates vector embeddings for a list of text chunks using efficient batching.
        Includes a robust async retry mechanism.
        """
        if not texts:
            return True, []

        all_embeddings = []
        async with self.semaphore:
            for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch = texts[i:i + EMBEDDING_BATCH_SIZE]
                for attempt in range(MAX_RETRIES):
                    try:
                        if self.config.is_test_mode:
                            logging.info(f"TEST_MODE_LOG: Calling get_embeddings API for batch {i//EMBEDDING_BATCH_SIZE + 1} ({len(batch)} texts)...")
                        
                        response = await asyncio.to_thread(self.embedding_model.get_embeddings, batch)
                        embeddings_from_batch = [embedding.values for embedding in response]
                        all_embeddings.extend(embeddings_from_batch)
                        
                        if self.config.is_test_mode:
                             logging.info(f"TEST_MODE_LOG: get_embeddings API call successful for batch {i//EMBEDDING_BATCH_SIZE + 1}.")
                        break  # Success, exit retry loop for this batch
                    
                    except api_core_exceptions.GoogleAPICallError as e:
                        wait_time = 2 ** attempt
                        logging.warning(f"Embedding batch {i//EMBEDDING_BATCH_SIZE + 1} failed with API Error (Code: {e.code}). Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    except Exception as e:
                        wait_time = 2 ** attempt
                        logging.error(f"Embedding batch {i//EMBEDDING_BATCH_SIZE + 1} failed with a non-API exception. Retrying in {wait_time}s...", exc_info=True)
                        await asyncio.sleep(wait_time)

                    if attempt == MAX_RETRIES - 1:
                        logging.critical(f"Embedding for batch starting at index {i} failed after all retries.")
                        return False, all_embeddings

        logging.info(f"Successfully generated {len(all_embeddings)} embeddings for {len(texts)} texts.")
        return True, all_embeddings

    async def generate_json_response(self, prompt: str, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates a JSON response from the AI model, enforcing a specific schema.
        Implements an async retry loop with exponential backoff and connection limiting.
        """
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
                        logging.warning(f"Attempt {attempt+1} finished with non-OK reason: '{finish_reason}'. Retrying...")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    
                    response_json = json.loads(response.text)
                    logging.info(f"Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                    return response_json

                except api_core_exceptions.GoogleAPICallError as e:
                    wait_time = 2 ** attempt
                    logging.warning(f"Generation attempt {attempt + 1} failed with Google API Error (Code: {e.code}). Retrying in {wait_time}s...")
                except Exception as e:
                    wait_time = 2 ** attempt
                    logging.error(f"Generation attempt {attempt + 1} failed with a non-API exception. Retrying in {wait_time}s...", exc_info=True)
                
                if attempt == MAX_RETRIES - 1:
                    logging.critical("AI generation failed after all retries.", exc_info=True)
                    raise
                
                await asyncio.sleep(2 ** attempt)
        
        raise RuntimeError("AI generation failed after all retries without raising a final exception.")