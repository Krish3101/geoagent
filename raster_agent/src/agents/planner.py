from src.agents.base import BaseAgent
from src.agents.schema import GeoTask
from src.core.llm import llm_client
from typing import Dict, Any, List

class PlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="PlannerAgent")

    def _build_system_prompt(self) -> str:
        return """You are the GeoAgent Planner.
Your job is to look at a parsed GeoTask and break it down into a linear execution plan of tools.
Output purely a JSON array of strings representing the tool sequence.

Available Tools:
- geocode
- fetch_raster
"""

    async def act(self, prompt: str, context: Dict[str, Any]) -> List[str]:
        task: GeoTask = context.get("task")
        if not task:
            raise ValueError("Planner requires a valid parsed task in context.")
            
        system = self._build_system_prompt()
        user_prompt = f"Create a tool sequence for this task:\n{task.model_dump_json()}"
        
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            plan = await llm_client.generate(messages, response_format="json")
            if not isinstance(plan, dict) or "sequence" not in plan:
                # Assuming the LLM returns {"sequence": ["geocode", ...]} 
                # If it just returns list directly:
                if isinstance(plan, list):
                    return plan
                raise Exception("Plan output format invalid.")
            return plan["sequence"]
        except Exception as e:
            # Fallback deterministic planner
            sequence = []
            if not task.geometry:
                sequence.append("geocode")
                
            has_raster = any(t in task.data_types for t in ["true_color", "ndvi", "aerial", "red", "green", "blue", "nir", "scl"])
            
            if has_raster:
                sequence.append("fetch_raster")
                
            return sequence
