# src/audit/stages/stage_1_general.py
import logging
import json

from src.config import AppConfig
from src.clients.ai_client import AiClient

class Chapter1Runner:
    """Handles generating content for Chapter 1 subchapters 1.2, 1.4, and 1.5."""
    STAGE_NAME = "Chapter-1"
    PROMPT_PATH = "assets/prompts/stage_chapter_1.txt"
    SCHEMA_PATH = "assets/schemas/chapter_1_schema.json"

    def __init__(self, config: AppConfig, ai_client: AiClient):
        self.config = config
        self.ai_client = ai_client
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    async def run(self) -> dict:
        """Executes the generation logic for Chapter 1.
        
        Returns:
            A dictionary with the AI-generated content for the subchapters.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        prompt_template = self._load_asset_text(self.PROMPT_PATH)
        schema = self._load_asset_json(self.SCHEMA_PATH)

        prompt = prompt_template.format(
            audit_type=self.config.audit_type,
            customer_id=self.config.customer_id
        )

        generated_data = await self.ai_client.generate_json_response(
            prompt=prompt,
            json_schema=schema
        )

        logging.info(f"Successfully generated data for stage {self.STAGE_NAME}")
        return generated_data

    def _load_asset_text(self, path: str) -> str:
        """Loads a text asset from the local filesystem."""
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def _load_asset_json(self, path: str) -> dict:
        """Loads a JSON asset from the local filesystem."""
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)