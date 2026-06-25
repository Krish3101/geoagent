from src.core.config import settings
from shared import OpenRouterClient

llm_client = OpenRouterClient(
    api_key=settings.OPENROUTER_API_KEY,
    project_name=settings.PROJECT_NAME,
    model=settings.LLM_MODEL
)

