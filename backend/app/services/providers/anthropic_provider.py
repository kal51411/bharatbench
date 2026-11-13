"""Anthropic Claude provider adapter."""
import logging
import time
from typing import List, Optional

from app.core.config import settings
from app.services.providers.base import BaseLLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

ANTHROPIC_MODELS = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-3-5",
    "claude-3-opus-20240229",
]


class AnthropicProvider(BaseLLMProvider):
    PROVIDER_NAME = "anthropic"

    def __init__(self):
        self._client = None

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Anthropic Claude"

    def get_available_models(self) -> List[str]:
        return ANTHROPIC_MODELS

    def is_configured(self) -> bool:
        return bool(settings.ANTHROPIC_API_KEY)

    def validate_configuration(self) -> tuple[bool, Optional[str]]:
        if not settings.ANTHROPIC_API_KEY:
            return False, "ANTHROPIC_API_KEY environment variable is not set."
        return True, None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            except ImportError:
                raise RuntimeError("anthropic package not installed")
        return self._client

    async def generate(self, request: LLMRequest, model_name: str) -> LLMResponse:
        if not self.is_configured():
            return LLMResponse(text="", model_name=model_name, error="ANTHROPIC_API_KEY not configured.")

        client = self._get_client()
        start = time.monotonic()
        try:
            kwargs = dict(
                model=model_name,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                messages=[{"role": "user", "content": request.prompt}],
            )
            if request.system_prompt:
                kwargs["system"] = request.system_prompt

            response = await client.messages.create(**kwargs)
            latency_ms = int((time.monotonic() - start) * 1000)

            text = response.content[0].text if response.content else ""
            return LLMResponse(
                text=text,
                model_name=model_name,
                model_version=response.model,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
                latency_ms=latency_ms,
                is_demo=False,
            )
        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.error(f"Anthropic generation error: {type(e).__name__}")
            return LLMResponse(text="", model_name=model_name, latency_ms=latency_ms, error=str(e))
