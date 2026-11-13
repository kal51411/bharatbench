"""Experiment routes."""
import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.base import get_db
from app.models.models import User, ModelRun, Evaluation, Question
from app.schemas.schemas import (
    ExperimentCreate, ExperimentPublic, ExperimentProgress,
    ModelRunPublic, PaginatedResponse, EvaluationPublic, MessageResponse
)
from app.services.experiment_service import experiment_service
from app.api.v1.deps import get_current_user, get_pagination

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.post("", response_model=ExperimentPublic, status_code=201)
async def create_experiment(
    exp_in: ExperimentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    experiment = await experiment_service.create_experiment(db, exp_in, current_user.id)
    return ExperimentPublic.model_validate(experiment)


@router.get("", response_model=PaginatedResponse)
async def list_experiments(
    pagination: tuple = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip, limit = pagination
    experiments, total = await experiment_service.list_experiments(db, skip, limit)
    return PaginatedResponse(
        items=[ExperimentPublic.model_validate(e) for e in experiments],
        total=total,
        page=(skip // limit) + 1,
        page_size=limit,
        total_pages=math.ceil(total / limit) if limit else 1,
    )


@router.get("/{experiment_id}", response_model=ExperimentPublic)
async def get_experiment(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    experiment = await experiment_service.get_experiment(db, experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return ExperimentPublic.model_validate(experiment)


@router.post("/{experiment_id}/run", response_model=MessageResponse)
async def run_experiment(
    experiment_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start experiment execution. Runs in background."""
    experiment = await experiment_service.get_experiment(db, experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    # Schedule async background execution
    async def run_in_background():
        from app.db.base import AsyncSessionLocal
        async with AsyncSessionLocal() as bg_db:
            try:
                await experiment_service.run_experiment(bg_db, experiment_id)
                await bg_db.commit()
            except Exception as e:
                await bg_db.rollback()

    background_tasks.add_task(run_in_background)
    return MessageResponse(message=f"Experiment {experiment_id} queued for execution")


@router.post("/{experiment_id}/cancel", response_model=ExperimentPublic)
async def cancel_experiment(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        experiment = await experiment_service.cancel_experiment(db, experiment_id)
        return ExperimentPublic.model_validate(experiment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{experiment_id}/retry-failed", response_model=MessageResponse)
async def retry_failed(
    experiment_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = await experiment_service.retry_failed(db, experiment_id)

    async def run_in_background():
        from app.db.base import AsyncSessionLocal
        async with AsyncSessionLocal() as bg_db:
            try:
                await experiment_service.run_experiment(bg_db, experiment_id)
                await bg_db.commit()
            except Exception:
                await bg_db.rollback()

    if count > 0:
        background_tasks.add_task(run_in_background)
    return MessageResponse(message=f"Reset {count} failed runs for retry")


@router.get("/{experiment_id}/progress", response_model=ExperimentProgress)
async def get_progress(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await experiment_service.get_progress(db, experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{experiment_id}/results", response_model=PaginatedResponse)
async def get_results(
    experiment_id: str,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    language: Optional[str] = None,
    category: Optional[str] = None,
    response_status: Optional[str] = None,
    pagination: tuple = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip, limit = pagination
    q = select(ModelRun).where(ModelRun.experiment_id == experiment_id)
    count_q = select(func.count(ModelRun.id)).where(ModelRun.experiment_id == experiment_id)

    if provider:
        q = q.where(ModelRun.provider == provider)
        count_q = count_q.where(ModelRun.provider == provider)
    if model_name:
        q = q.where(ModelRun.model_name == model_name)
        count_q = count_q.where(ModelRun.model_name == model_name)
    if language or category:
        q = q.join(Question, Question.id == ModelRun.question_id)
        count_q = count_q.join(Question, Question.id == ModelRun.question_id)
        if language:
            q = q.where(Question.language == language)
            count_q = count_q.where(Question.language == language)
        if category:
            q = q.where(Question.category == category)
            count_q = count_q.where(Question.category == category)
    if response_status:
        q = q.where(ModelRun.response_status == response_status)
        count_q = count_q.where(ModelRun.response_status == response_status)

    total = (await db.execute(count_q)).scalar_one()
    result = await db.execute(q.order_by(ModelRun.created_at.desc()).offset(skip).limit(limit))
    runs = result.scalars().all()

    return PaginatedResponse(
        items=[ModelRunPublic.model_validate(r) for r in runs],
        total=total,
        page=(skip // limit) + 1,
        page_size=limit,
        total_pages=math.ceil(total / limit) if limit else 1,
    )
