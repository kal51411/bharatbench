"""Dataset routes."""
import io
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import math

from app.db.base import get_db
from app.models.models import User, Question, Dataset
from app.schemas.schemas import (
    DatasetCreate, DatasetPublic, DatasetValidationResult,
    QuestionPublic, PaginatedResponse, MessageResponse
)
from app.services.dataset_service import dataset_service
from app.api.v1.deps import get_current_user, get_pagination

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("/template", response_class=StreamingResponse)
async def download_template():
    """Download the CSV dataset template."""
    template = dataset_service.get_csv_template()
    return StreamingResponse(
        io.BytesIO(template.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bharatbench_template.csv"},
    )


@router.post("/validate", response_model=DatasetValidationResult)
async def validate_dataset(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Validate a CSV or JSON file without importing it."""
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File exceeds 10MB limit")

    fname = (file.filename or "").lower()
    if fname.endswith(".json"):
        return dataset_service.validate_json(content)
    elif fname.endswith(".csv"):
        return dataset_service.validate_csv(content)
    else:
        raise HTTPException(status_code=400, detail="Only CSV or JSON files are supported")


@router.post("/import", response_model=DatasetPublic)
async def import_dataset(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: Optional[str] = Form(default=None),
    version: str = Form(default="1.0"),
    skip_invalid: bool = Form(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import a CSV or JSON dataset file."""
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File exceeds 10MB limit")

    fname = (file.filename or "").lower()
    if fname.endswith(".json"):
        fmt = "json"
    elif fname.endswith(".csv"):
        fmt = "csv"
    else:
        raise HTTPException(status_code=400, detail="Only CSV or JSON files are supported")

    # Validate first
    validation = dataset_service.validate_csv(content) if fmt == "csv" else dataset_service.validate_json(content)
    if validation.invalid_rows > 0 and not skip_invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Dataset has {validation.invalid_rows} invalid rows. Fix errors or set skip_invalid=true. First error: {validation.errors[0].message if validation.errors else 'unknown'}",
        )

    dataset_in = DatasetCreate(name=name, description=description, version=version)
    dataset = await dataset_service.create_dataset(db, dataset_in, current_user.id)
    imported, skipped = await dataset_service.import_records(db, dataset, content, fmt, skip_invalid)
    return DatasetPublic.model_validate(dataset)


@router.post("", response_model=DatasetPublic, status_code=201)
async def create_dataset(
    dataset_in: DatasetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create an empty dataset."""
    dataset = await dataset_service.create_dataset(db, dataset_in, current_user.id)
    return DatasetPublic.model_validate(dataset)


@router.get("", response_model=PaginatedResponse)
async def list_datasets(
    pagination: tuple = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip, limit = pagination
    datasets, total = await dataset_service.list_datasets(db, skip, limit)
    return PaginatedResponse(
        items=[DatasetPublic.model_validate(d) for d in datasets],
        total=total,
        page=(skip // limit) + 1,
        page_size=limit,
        total_pages=math.ceil(total / limit) if limit else 1,
    )


@router.get("/{dataset_id}", response_model=DatasetPublic)
async def get_dataset(
    dataset_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dataset = await dataset_service.get_dataset(db, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return DatasetPublic.model_validate(dataset)


@router.get("/{dataset_id}/questions", response_model=PaginatedResponse)
async def get_dataset_questions(
    dataset_id: str,
    pagination: tuple = Depends(get_pagination),
    language: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import func
    dataset = await dataset_service.get_dataset(db, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    skip, limit = pagination
    q = select(Question).where(Question.dataset_id == dataset_id)
    count_q = select(func.count(Question.id)).where(Question.dataset_id == dataset_id)
    if language:
        q = q.where(Question.language == language)
        count_q = count_q.where(Question.language == language)
    if category:
        q = q.where(Question.category == category)
        count_q = count_q.where(Question.category == category)

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


@router.post("/{dataset_id}/publish", response_model=DatasetPublic)
async def publish_dataset(
    dataset_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        dataset = await dataset_service.publish_dataset(db, dataset_id)
        return DatasetPublic.model_validate(dataset)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{dataset_id}/export")
async def export_dataset(
    dataset_id: str,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export dataset questions as CSV or JSON."""
    import csv
    import json
    dataset = await dataset_service.get_dataset(db, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    result = await db.execute(select(Question).where(Question.dataset_id == dataset_id))
    questions = result.scalars().all()

    if format == "json":
        data = [
            {
                "external_question_id": q.external_question_id,
                "comparison_group_id": q.comparison_group_id,
                "question_text": q.question_text,
                "language": q.language.value,
                "category": q.category.value,
                "context": q.context,
                "question_type": q.question_type.value,
                "reference_answer": q.reference_answer,
                "acceptable_answers": q.acceptable_answers,
                "evaluation_rubric": q.evaluation_rubric,
            }
            for q in questions
        ]
        return StreamingResponse(
            io.BytesIO(json.dumps({"questions": data}, ensure_ascii=False, indent=2).encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=dataset_{dataset_id}.json"},
        )

    # CSV
    output = io.StringIO()
    fieldnames = [
        "external_question_id", "comparison_group_id", "question_text", "language",
        "category", "context", "question_type", "reference_answer", "acceptable_answers", "evaluation_rubric"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for q in questions:
        writer.writerow({
            "external_question_id": q.external_question_id or "",
            "comparison_group_id": q.comparison_group_id or "",
            "question_text": q.question_text,
            "language": q.language.value,
            "category": q.category.value,
            "context": q.context or "",
            "question_type": q.question_type.value,
            "reference_answer": q.reference_answer or "",
            "acceptable_answers": json.dumps(q.acceptable_answers) if q.acceptable_answers else "",
            "evaluation_rubric": q.evaluation_rubric or "",
        })
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=dataset_{dataset_id}.csv"},
    )
