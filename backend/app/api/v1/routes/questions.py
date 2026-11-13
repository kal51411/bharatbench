"""Questions routes."""
import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.base import get_db
from app.models.models import User, Question
from app.schemas.schemas import QuestionPublic, PaginatedResponse
from app.api.v1.deps import get_current_user, get_pagination

router = APIRouter(prefix="/questions", tags=["questions"])


@router.get("", response_model=PaginatedResponse)
async def list_questions(
    dataset_id: Optional[str] = None,
    language: Optional[str] = None,
    category: Optional[str] = None,
    comparison_group_id: Optional[str] = None,
    pagination: tuple = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip, limit = pagination
    q = select(Question)
    count_q = select(func.count(Question.id))

    if dataset_id:
        q = q.where(Question.dataset_id == dataset_id)
        count_q = count_q.where(Question.dataset_id == dataset_id)
    if language:
        q = q.where(Question.language == language)
        count_q = count_q.where(Question.language == language)
    if category:
        q = q.where(Question.category == category)
        count_q = count_q.where(Question.category == category)
    if comparison_group_id:
        q = q.where(Question.comparison_group_id == comparison_group_id)
        count_q = count_q.where(Question.comparison_group_id == comparison_group_id)

    total = (await db.execute(count_q)).scalar_one()
    result = await db.execute(q.offset(skip).limit(limit))
    questions = result.scalars().all()

    return PaginatedResponse(
        items=[QuestionPublic.model_validate(q) for q in questions],
        total=total,
        page=(skip // limit) + 1,
        page_size=limit,
        total_pages=math.ceil(total / limit) if limit else 1,
    )


@router.get("/{question_id}", response_model=QuestionPublic)
async def get_question(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return QuestionPublic.model_validate(question)
