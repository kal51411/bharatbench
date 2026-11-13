"""Evaluation routes."""
import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.models import User
from app.schemas.schemas import (
    EvaluationCreate, EvaluationPublic, EvaluationUpdate,
    PaginatedResponse, MessageResponse
)
from app.services.evaluation_service import evaluation_service
from app.api.v1.deps import get_current_user, get_pagination

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.post("", response_model=EvaluationPublic, status_code=201)
async def create_evaluation(
    eval_in: EvaluationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        evaluation = await evaluation_service.create_human_evaluation(db, eval_in, current_user.id)
        return EvaluationPublic.model_validate(evaluation)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=PaginatedResponse)
async def list_evaluations(
    experiment_id: Optional[str] = None,
    review_status: Optional[str] = None,
    pagination: tuple = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip, limit = pagination
    evaluations, total = await evaluation_service.list_evaluations(
        db, skip, limit, experiment_id, review_status
    )
    return PaginatedResponse(
        items=[EvaluationPublic.model_validate(e) for e in evaluations],
        total=total,
        page=(skip // limit) + 1,
        page_size=limit,
        total_pages=math.ceil(total / limit) if limit else 1,
    )


@router.get("/{evaluation_id}", response_model=EvaluationPublic)
async def get_evaluation(
    evaluation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    evaluation = await evaluation_service.get_evaluation(db, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return EvaluationPublic.model_validate(evaluation)


@router.patch("/{evaluation_id}", response_model=EvaluationPublic)
async def update_evaluation(
    evaluation_id: str,
    update_in: EvaluationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        evaluation = await evaluation_service.update_evaluation(
            db, evaluation_id, update_in, current_user.id
        )
        return EvaluationPublic.model_validate(evaluation)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/pending/count", response_model=dict)
async def get_pending_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = await evaluation_service.get_pending_review_count(db)
    return {"pending_count": count}
