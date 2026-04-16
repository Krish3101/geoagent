from typing import Callable, Dict, Any, Type
from pydantic import BaseModel

class Tool:
    def __init__(self, name: str, description: str, func: Callable, schema: Type[BaseModel]):
        self.name = name
        self.description = description
        self.func = func
        self.schema = schema

    async def execute(self, **kwargs) -> Any:
        # Validate arguments using the pydantic schema
        validated_args = self.schema(**kwargs)
        return await self.func(**validated_args.model_dump())

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool):
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name} already registered.")
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Tool {name} not found.")
        return self._tools[name]
        
    def get_all_tools(self) -> Dict[str, Tool]:
        return self._tools

registry = ToolRegistry()
