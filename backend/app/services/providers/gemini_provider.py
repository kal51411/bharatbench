"""Google Gemini provider adapter."""
import logging
import time
from typing import List, Optional

from app.core.config import settings
from app.services.providers.base import BaseLLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]


class GeminiProvider(BaseLLMProvider):
    PROVIDER_NAME = "gemini"

    def __init__(self):
        self._client = None

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Google Gemini"

    def get_available_models(self) -> List[str]:
        return GEMINI_MODELS

    def is_configured(self) -> bool:
        return bool(settings.GEMINI_API_KEY)

    def validate_configuration(self) -> tuple[bool, Optional[str]]:
        if not settings.GEMINI_API_KEY:
            return False, "GEMINI_API_KEY environment variable is not set."
        return True, None

    def _get_client(self):
        if self._client is None:
            try:
                import google.generativeai as genai
                genai.configure(api_key=settings.GEMINI_API_KEY)
                self._client = genai
            except ImportError:
                raise RuntimeError("google-generativeai package not installed")
        return self._client

    async def generate(self, request: LLMRequest, model_name: str) -> LLMResponse:
        if not self.is_configured():
            return LLMResponse(text="", model_name=model_name, error="GEMINI_API_KEY not configured.")

        import asyncio
        genai = self._get_client()
        start = time.monotonic()

        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=request.system_prompt,
            )
            generation_config = genai.types.GenerationConfig(
                temperature=request.temperature,
                max_output_tokens=request.max_tokens,
            )
            # Run synchronous call in thread pool to not block event loop
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    request.prompt,
                    generation_config=generation_config,
                )
            )
            latency_ms = int((time.monotonic() - start) * 1000)

            text = response.text if hasattr(response, "text") else ""
            input_tokens = None
            output_tokens = None
            if hasattr(response, "usage_metadata"):
                input_tokens = getattr(response.usage_metadata, "prompt_token_count", None)
                output_tokens = getattr(response.usage_metadata, "candidates_token_count", None)

            return LLMResponse(
                text=text,
                model_name=model_name,
                model_version=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                is_demo=False,
            )
        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.error(f"Gemini generation error: {type(e).__name__}")
            return LLMResponse(text="", model_name=model_name, latency_ms=latency_ms, error=str(e))
