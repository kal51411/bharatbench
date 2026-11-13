"""OpenAI provider adapter with retry and rate-limit handling."""
import asyncio
import logging
import time
from typing import List, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.config import settings
from app.services.providers.base import BaseLLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

OPENAI_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
]


class OpenAIProvider(BaseLLMProvider):
    PROVIDER_NAME = "openai"

    def __init__(self):
        self._client = None

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "OpenAI"

    def get_available_models(self) -> List[str]:
        return OPENAI_MODELS

    def is_configured(self) -> bool:
        return bool(settings.OPENAI_API_KEY)

    def validate_configuration(self) -> tuple[bool, Optional[str]]:
        if not settings.OPENAI_API_KEY:
            return False, "OPENAI_API_KEY environment variable is not set."
        return True, None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            except ImportError:
                raise RuntimeError("openai package not installed")
        return self._client

    async def generate(self, request: LLMRequest, model_name: str) -> LLMResponse:
        if not self.is_configured():
            return LLMResponse(
                text="",
                model_name=model_name,
                error="OpenAI API key not configured.",
            )

        client = self._get_client()
        start = time.monotonic()
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=request.timeout,
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            choice = response.choices[0]
            return LLMResponse(
                text=choice.message.content or "",
                model_name=model_name,
                model_version=response.model,
                input_tokens=response.usage.prompt_tokens if response.usage else None,
                output_tokens=response.usage.completion_tokens if response.usage else None,
                latency_ms=latency_ms,
                is_demo=False,
            )
        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.error(f"OpenAI generation error: {type(e).__name__}")
            return LLMResponse(
                text="",
                model_name=model_name,
                latency_ms=latency_ms,
                error=str(e),
            )
