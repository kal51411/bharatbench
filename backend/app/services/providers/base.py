"""Base LLM Provider interface and provider registry."""
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    text: str
    model_name: str
    model_version: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    is_demo: bool = False
    raw_response: Optional[Any] = None
    error: Optional[str] = None


@dataclass
class LLMRequest:
    """Standardized request to any LLM provider."""
    prompt: str
    system_prompt: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: int = 60
    extra_params: Dict[str, Any] = field(default_factory=dict)


class BaseLLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier for this provider."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable display name."""
        ...

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """Return list of available model identifiers."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the provider has valid credentials."""
        ...

    @abstractmethod
    def validate_configuration(self) -> tuple[bool, Optional[str]]:
        """Validate credentials. Returns (success, error_message)."""
        ...

    @abstractmethod
    async def generate(self, request: LLMRequest, model_name: str) -> LLMResponse:
        """Generate a response for the given request."""
        ...

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "provider": self.provider_name,
            "display_name": self.display_name,
            "models": self.get_available_models(),
            "is_configured": self.is_configured(),
        }


# ─────────────────────────── PROVIDER REGISTRY ───────────────────────────

class ProviderRegistry:
    """Registry for LLM providers. Lazily loads providers on first access."""

    def __init__(self):
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._initialized = False

    def _load_providers(self):
        if self._initialized:
            return
        self._initialized = True

        # Always register the mock provider
        from app.services.providers.mock_provider import MockLLMProvider
        mock = MockLLMProvider()
        self._providers[mock.provider_name] = mock

        # Conditionally register real providers based on API key availability
        try:
            from app.services.providers.openai_provider import OpenAIProvider
            p = OpenAIProvider()
            self._providers[p.provider_name] = p
        except Exception as e:
            logger.warning(f"OpenAI provider not loaded: {e}")

        try:
            from app.services.providers.gemini_provider import GeminiProvider
            p = GeminiProvider()
            self._providers[p.provider_name] = p
        except Exception as e:
            logger.warning(f"Gemini provider not loaded: {e}")

        try:
            from app.services.providers.anthropic_provider import AnthropicProvider
            p = AnthropicProvider()
            self._providers[p.provider_name] = p
        except Exception as e:
            logger.warning(f"Anthropic provider not loaded: {e}")

    def get_provider(self, name: str) -> Optional[BaseLLMProvider]:
        self._load_providers()
        return self._providers.get(name)

    def list_providers(self) -> List[BaseLLMProvider]:
        self._load_providers()
        return list(self._providers.values())

    def get_provider_names(self) -> List[str]:
        self._load_providers()
        return list(self._providers.keys())

    def reset(self):
        """Reset for testing."""
        self._providers = {}
        self._initialized = False


# Global singleton registry
provider_registry = ProviderRegistry()
