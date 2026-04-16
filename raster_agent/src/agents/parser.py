import json
from src.agents.base import BaseAgent
from src.agents.schema import GeoTask, TaskValidationError
from src.core.llm import llm_client
from typing import Dict, Any

class ParsingAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="ParsingAgent")
        
    def _build_system_prompt(self, context: str) -> str:
        return f"""You are GeoAgent, an AI specialized in understanding geospatial queries.
Your job is to parse the user's request into a strict JSON payload that matches the GeoTask schema.

CONTEXT:
{context}

RULES:
- Extract 'region_name', 'start_date' (YYYY-MM-DD), 'end_date' (YYYY-MM-DD), and 'data_types'.
- Valid data_types: true_color, ndvi, aerial, red, green, blue, nir, scl.
- Output ONLY valid JSON matching the schema criteria. No markdown blocks, just the raw JSON dict.
"""

    async def act(self, prompt: str, context: Dict[str, Any]) -> GeoTask:
        formatted_context = "No previous context."
        if context.get("last_task"):
            task_info = context["last_task"].copy()
            if "geometry" in task_info:
                task_info["geometry"] = "[OMITTED FOR SIZE]"
            formatted_context = f"Previous Task:\n{json.dumps(task_info)}"
            
        system = self._build_system_prompt(formatted_context)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
        
        try:
            # We enforce JSON mode via OpenRouter
            result = await llm_client.generate(messages, response_format="json")
            
            # Validate using Pydantic
            task = GeoTask(**result)
            return task
            
        except Exception as e:
            # Pydantic validation errors will be caught here and raised as TaskValidationError if needed
            if isinstance(e, TaskValidationError):
                raise
            raise TaskValidationError(f"Failed to parse or validate LLM output: {str(e)}")
