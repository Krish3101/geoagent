from typing import Any, Dict, List, Optional
from src.tools.registry import registry
from pydantic import BaseModel

class BaseAgent:
    def __init__(self, name: str, model: str = "anthropic/claude-3.5-sonnet:beta", tools: Optional[List[str]] = None):
        """
        Base Agent capable of acting as an intelligent orchestrator.
        `tools` is a list of tool names registered in the central ToolRegistry.
        """
        self.name = name
        self.model = model
        self.available_tools = {}
        
        if tools:
            for tool_name in tools:
                self.available_tools[tool_name] = registry.get_tool(tool_name)

    async def act(self, prompt: str, context: Dict[str, Any]) -> Any:
        # To be implemented by subclasses or a general LLM loop
        raise NotImplementedError("Subclasses must implement the act method.")
        
    def get_system_prompt(self) -> str:
        # Load from src/agents/prompts/
        pass
