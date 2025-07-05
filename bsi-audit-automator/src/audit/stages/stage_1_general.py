# src/audit/stages/stage_1_general.py
import logging
import json
import asyncio
from typing import Dict, Any

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

class Chapter1Runner:
    """Handles generating content for Chapter 1, with 1.4 being a manual placeholder."""
    STAGE_NAME = "Chapter-1"

    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.ai_client = ai_client
        self.rag_client = rag_client
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f: return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    async def _process_geltungsbereich(self) -> Dict[str, Any]:
        """Handles 1.2 Geltungsbereich and 1.4 Informationsverbund using a filtered RAG query."""
        logging.info("Processing 1.2 Geltungsbereich and 1.4 Informationsverbund...")
        queries = ["Name, Umfang und Abgrenzung des Informationsverbunds, betroffene Geschäftsprozesse, Standorte und Anwendungen.", "Kurzbezeichnung und Kurzbeschreibung des Informationsverbunds."]
        
        # **FIX**: Apply category filtering to narrow the search to relevant documents.
        context = self.rag_client.get_context_for_query(
            queries,
            source_categories=['Informationsverbund', 'Strukturanalyse']
        )
        
        if "No relevant context found" in context:
            logging.warning("No RAG context found for Geltungsbereich. Generating deterministic response.")
            return {
                "kurzbezeichnung": "Nicht ermittelt",
                "kurzbeschreibung": "Nicht ermittelt",
                "description": "Der Geltungsbereich des Informationsverbunds konnte aus den bereitgestellten Dokumenten nicht eindeutig ermittelt werden. Dies muss manuell geklärt und dokumentiert werden.",
                "finding": {
                    "category": "AS",
                    "description": "Die Abgrenzung des Geltungsbereichs ist unklar, da keine Dokumente gefunden wurden, die diesen beschreiben. Dies ist eine schwerwiegende Abweichung, die vor dem Audit geklärt werden muss."
                }
            }
            
        prompt_template = self._load_asset_text("assets/prompts/stage_1_2_geltungsbereich.txt")
        schema = self._load_asset_json("assets/schemas/stage_1_2_geltungsbereich_schema.json")
        
        prompt = prompt_template.format(context=context)
        return await self.ai_client.generate_json_response(prompt, schema)

    async def run(self) -> dict:
        """Executes the generation logic for Chapter 1."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        geltungsbereich_result = await self._process_geltungsbereich()

        final_result = {
            "verfasser": {
                "name": "Dixie"
            },
            "geltungsbereichDerZertifizierung": geltungsbereich_result,
            "auditierteInstitution": {},
            "grundlageDesAudits": {},
            "audittyp": {
                "content": self.config.audit_type
            },
            "auditplan": {}
        }

        logging.info(f"Successfully generated data for stage {self.STAGE_NAME}")
        return final_result