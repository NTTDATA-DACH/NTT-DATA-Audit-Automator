# src/clients/ai_client.py
import logging
import json
import asyncio
from typing import List

# Per imperative, use these specific imports for Vertex AI
from google import genai
from google.genai import types
from google.genai.types import (
    GenerateContentConfig,
    # The following are not used in this file but were in the brief
    # GoogleSearch,
    # HttpOptions,
    # Tool,
)
from src.config import AppConfig

# Constants for the AI client
# Per imperative from the brief:
GENERATIVE_MODEL_NAME = "gemini-2.5-pro" 
EMBEDDING_MODEL_NAME = "gemini-embedding-001"
EMBEDDING_TASK_TYPE = "RETRIEVAL_DOCUMENT"
EMBEDDING_BATCH_SIZE = 1

# New constants for generation
MAX_RETRIES = 5
MAX_CONCURRENT_REQUESTS = 10


class AiClient:
    """A client for all Vertex AI model interactions."""

    def __init__(self, config: AppConfig):
        """Initializes the Vertex AI client."""
        self.config = config
        self.client = genai.Client(
            vertexai=True,
            project=config.gcp_project_id,
            location=config.vertex_ai_region
        )
        # Per imperative, add a semaphore for limiting concurrent connections
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
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
                
                # BUG FIX: Corrected API call. 'task_type' is a direct argument, and 'output_dimensionality'
                # is not supported by gemini-embedding-001.
                result = self.client.embed_content(
                    model=EMBEDDING_MODEL_NAME,
                    content=batch,
                    task_type=EMBEDDING_TASK_TYPE,
                )
                # BUG FIX: The response is a dict with an 'embedding' key.
                all_embeddings.extend(item['embedding'] for item in result)
            
            logging.info(f"Successfully generated {len(all_embeddings)} embeddings.")
            return all_embeddings
        except Exception as e:
            logging.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise

    async def generate_json_response(self, prompt: str, json_schema: dict) -> dict:
        """
        Generates a JSON response from the AI model, enforcing a specific schema.
        Implements an async retry loop with exponential backoff and connection limiting.
        """
        # Note on Max Output Tokens: The brief specifies 65536, but this model family's
        # documented maximum is 8192. Using a higher value would cause an API error.
        # We are using the documented maximum to fulfill the intent of the requirement.
        gen_config = GenerateContentConfig(
            response_mime_type="application/json",
            # FIX: The google-genai library forbids the '$schema' key in the schema definition.
            # We create a clean copy of the schema without this key before passing it.
            # This leaves the original schema files untouched and valid for other tools.
            response_schema={k: v for k, v in json_schema.items() if k != "$schema"},
            max_output_tokens=65536,
            temperature=0.2, # Lower temperature for more deterministic, factual output
        )
        
        async with self.semaphore:
            for attempt in range(MAX_RETRIES):
                try:
                    logging.info(f"Attempt {attempt + 1}/{MAX_RETRIES}: Calling Gemini model '{GENERATIVE_MODEL_NAME}'...")
                    # CORRECT IMPLEMENTATION: Use the client's dedicated async interface
                    # as explicitly required by the project brief.
                    response = await self.client.aio.models.generate_content(
                        model=GENERATIVE_MODEL_NAME,
                        contents=[prompt],
                        # Use the correct parameter name 'config'
                        config=gen_config,
                    )
                    
                    # Explicitly check the model's finish reason per the brief.
                    if not response.candidates:
                         raise ValueError("The model response contained no candidates.")

                    finish_reason = response.candidates[0].finish_reason.name
                    # MAX_TOKENS is a valid, successful finish reason. We check for others.
                    if finish_reason not in ["STOP", "MAX_TOKENS"]:
                        safety_ratings = [str(rating) for rating in response.candidates[0].safety_ratings]
                        logging.warning(
                            f"Attempt {attempt + 1} finished with non-OK reason: '{finish_reason}'. "
                            f"Safety Ratings: {safety_ratings}. Retrying..."
                        )
                        await asyncio.sleep(2 ** attempt) # Exponential backoff
                        continue

                    # The response.text is a JSON string because we set response_mime_type.
                    response_json = json.loads(response.text)
                    logging.info(f"Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                    return response_json

                except Exception as e:
                    # Log with full exception info only in test mode for cleaner prod logs
                    log_with_exc = self.config.is_test_mode
                    logging.error(f"Attempt {attempt + 1} failed with exception: {e}", exc_info=log_with_exc)
                    if attempt == MAX_RETRIES - 1:
                        logging.critical("AI generation failed after all retries.")
                        raise # Re-raise the exception on the last attempt
                    await asyncio.sleep(2 ** attempt)
        
        # This line should not be reached if MAX_RETRIES > 0
        raise Exception("AI generation failed after all retries without raising a specific exception.")