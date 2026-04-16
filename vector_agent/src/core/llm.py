import httpx
from typing import Dict, Any, List
from src.core.config import settings
import json

class OpenRouterClient:
    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        self.base_url = "https://openrouter.ai/api/v1"
        self.model = settings.LLM_MODEL
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": settings.PROJECT_NAME,
            "Content-Type": "application/json"
        }

    async def generate(self, messages: List[Dict[str, str]], response_format: str = None) -> Any:
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not set in environment.")
            
        payload = {
            "model": self.model,
            "messages": messages,
        }
        
        # Many models (like Gemma-3) on OpenRouter don't support the strict `json_object` format flag.
        # So we skip payload["response_format"] = {"type": "json_object"} to prevent 400 errors.

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=60.0
            )
            
            response.raise_for_status()
            data = response.json()
            
            content = data["choices"][0]["message"].get("content", "")
            if content is None:
                content = "{}" if response_format == "json" else ""
            
            if response_format == "json":
                # Clean up markdown formatting if the model still wrapped it
                cleaned = content.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                return json.loads(cleaned.strip())
            return content

llm_client = OpenRouterClient()
