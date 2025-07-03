# src/audit/stages/stage_1_general.py
import logging
import json
import asyncio
from typing import Dict, Any

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

class Chapter1Runner:
    """Handles generating content for Chapter 1 subchapters 1.2, 1.4, and 1.5."""
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
        """Handles 1.2 Geltungsbereich der Zertifizierung using RAG."""
        logging.info("Processing 1.2 Geltungsbereich der Zertifizierung...")
        query = "Umfang und Abgrenzung des Informationsverbunds, betroffene Geschäftsprozesse, Standorte und Anwendungen"
        context = self.rag_client.get_context_for_query(query)
        
        prompt_template = self._load_asset_text("assets/prompts/stage_1_2_geltungsbereich.txt")
        schema = self._load_asset_json("assets/schemas/stage_1_2_geltungsbereich_schema.json")
        
        prompt = prompt_template.format(context=context)
        return await self.ai_client.generate_json_response(prompt, schema)

    async def _process_audit_team(self) -> Dict[str, Any]:
        """Handles 1.4 Audit-Team using RAG."""
        logging.info("Processing 1.4 Audit-Team...")
        query = "Namen und Rollen des Auditteams, Auditoren, Prüfer oder Mitglieder des Audits"
        context = self.rag_client.get_context_for_query(query, num_neighbors=3)

        prompt_template = self._load_asset_text("assets/prompts/stage_1_4_audit_team.txt")
        schema = self._load_asset_json("assets/schemas/stage_1_4_audit_team_schema.json")

        prompt = prompt_template.format(context=context)
        return await self.ai_client.generate_json_response(prompt, schema)

    async def run(self) -> dict:
        """Executes the generation logic for Chapter 1."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        # Run RAG-based tasks in parallel
        rag_tasks = {
            "geltungsbereichDerZertifizierung": self._process_geltungsbereich(),
            "auditTeam": self._process_audit_team()
        }
        rag_results_list = await asyncio.gather(*rag_tasks.values())
        rag_results = dict(zip(rag_tasks.keys(), rag_results_list))

        # Final assembly including deterministic parts
        final_result = {
            "verfasser": {
                "name": "Dixie"
            },
            "geltungsbereichDerZertifizierung": rag_results["geltungsbereichDerZertifizierung"],
            "grundlageDesAudits": {
                "content": ""
            },
            "auditTeam": rag_results["auditTeam"],
            "audittyp": {
                "content": self.config.audit_type
            },
            "auditplan": {
                "content": ""
            }
        }

        logging.info(f"Successfully generated data for stage {self.STAGE_NAME}")
        return final_result