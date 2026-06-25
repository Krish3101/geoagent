import httpx
from typing import Dict, Any, List
import json

class OpenRouterClient:
    def __init__(self, api_key: str, project_name: str, model: str):
        self.api_key = api_key
        self.base_url = "https://openrouter.ai/api/v1"
        self.model = model
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": project_name,
            "Content-Type": "application/json"
        }

    async def generate(self, messages: List[Dict[str, str]], response_format: str = None) -> Any:
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not set in environment.")
            
        payload = {
            "model": self.model,
            "messages": messages,
        }

        async with httpx.AsyncClient() as client:
            url = f"{self.base_url}/chat/completions"
            response = await client.post(
                url,
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
                cleaned = content.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                return json.loads(cleaned.strip())
            return content
