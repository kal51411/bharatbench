"""Analytics routes."""
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.db.base import get_db
from app.models.models import User
from app.schemas.schemas import (
    OverviewStats, ModelAccuracy, LanguageAccuracy, CategoryAccuracy,
    BiasAnalysis, ConsistencyResult
)
from app.services.analytics_service import analytics_service
from app.api.v1.deps import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=OverviewStats)
async def get_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await analytics_service.get_overview(db)


@router.get("/models", response_model=List[ModelAccuracy])
async def get_model_accuracy(
    experiment_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await analytics_service.get_model_accuracy(db, experiment_id)


@router.get("/languages", response_model=List[LanguageAccuracy])
async def get_language_accuracy(
    experiment_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await analytics_service.get_language_accuracy(db, experiment_id)


@router.get("/categories", response_model=List[CategoryAccuracy])
async def get_category_accuracy(
    experiment_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await analytics_service.get_category_accuracy(db, experiment_id)


@router.get("/bias", response_model=BiasAnalysis)
async def get_bias_analysis(
    experiment_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await analytics_service.get_bias_analysis(db, experiment_id)


@router.get("/consistency", response_model=List[ConsistencyResult])
async def get_consistency(
    experiment_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await analytics_service.get_consistency_analysis(db, experiment_id)
