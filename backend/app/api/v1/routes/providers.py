"""Provider status routes."""
from fastapi import APIRouter, Depends
from typing import List
from app.models.models import User
from app.schemas.schemas import ProviderStatus, ProviderInfo
from app.services.providers.base import provider_registry
from app.api.v1.deps import get_current_user

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=List[ProviderInfo])
async def list_providers(current_user: User = Depends(get_current_user)):
    """List all registered providers and their configuration status."""
    providers = provider_registry.list_providers()
    return [
        ProviderInfo(
            provider=p.provider_name,
            display_name=p.display_name,
            models=p.get_available_models(),
            is_configured=p.is_configured(),
        )
        for p in providers
    ]


@router.get("/status", response_model=List[ProviderStatus])
async def get_provider_status(current_user: User = Depends(get_current_user)):
    """Get configuration and availability status of all providers.
    NOTE: Does not expose API keys."""
    providers = provider_registry.list_providers()
    statuses = []
    for p in providers:
        ok, err = p.validate_configuration()
        statuses.append(ProviderStatus(
            provider=p.provider_name,
            is_configured=p.is_configured(),
            is_available=ok,
            models=p.get_available_models(),
            error=err,
        ))
    return statuses
