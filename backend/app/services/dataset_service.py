"""Dataset management service: validation, import, versioning."""
import csv
import io
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
import chardet

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.models import Dataset, Question, Language, Category, QuestionType, DatasetStatus
from app.schemas.schemas import (
    DatasetCreate, QuestionCreate, DatasetValidationResult, ValidationError
)

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {"question_text", "language", "category"}
OPTIONAL_FIELDS = {
    "external_question_id", "comparison_group_id", "context",
    "question_type", "reference_answer", "acceptable_answers", "evaluation_rubric"
}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

VALID_LANGUAGES = {l.value for l in Language}
VALID_CATEGORIES = {c.value for c in Category}
VALID_QUESTION_TYPES = {qt.value for qt in QuestionType}


class DatasetService:

    async def create_dataset(
        self,
        db: AsyncSession,
        dataset_in: DatasetCreate,
        created_by_id: str,
    ) -> Dataset:
        """Create a new dataset record."""
        dataset = Dataset(
            name=dataset_in.name,
            description=dataset_in.description,
            version=dataset_in.version,
            created_by_id=created_by_id,
            status=DatasetStatus.draft,
        )
        db.add(dataset)
        await db.flush()
        await db.refresh(dataset)
        return dataset

    async def get_dataset(self, db: AsyncSession, dataset_id: str) -> Optional[Dataset]:
        result = await db.execute(select(Dataset).where(Dataset.id == dataset_id))
        return result.scalar_one_or_none()

    async def list_datasets(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Dataset], int]:
        count_result = await db.execute(select(func.count(Dataset.id)))
        total = count_result.scalar_one()
        result = await db.execute(
            select(Dataset).order_by(Dataset.created_at.desc()).offset(skip).limit(limit)
        )
        return result.scalars().all(), total

    async def publish_dataset(self, db: AsyncSession, dataset_id: str) -> Dataset:
        dataset = await self.get_dataset(db, dataset_id)
        if not dataset:
            raise ValueError(f"Dataset {dataset_id} not found")
        dataset.status = DatasetStatus.published
        await db.flush()
        return dataset

    def validate_csv(self, content: bytes) -> DatasetValidationResult:
        """Validate CSV content without importing it."""
        return self._validate_records(self._parse_csv(content))

    def validate_json(self, content: bytes) -> DatasetValidationResult:
        """Validate JSON content without importing it."""
        return self._validate_records(self._parse_json(content))

    def _parse_csv(self, content: bytes) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Parse CSV bytes, detecting encoding."""
        detected = chardet.detect(content)
        encoding = detected.get("encoding", "utf-8") or "utf-8"
        try:
            text = content.decode(encoding)
        except Exception:
            text = content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        parse_error = None
        try:
            rows = list(reader)
        except Exception as e:
            parse_error = str(e)
        return rows, parse_error

    def _parse_json(self, content: bytes) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Parse JSON bytes."""
        try:
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, list):
                return data, None
            elif isinstance(data, dict) and "questions" in data:
                return data["questions"], None
            else:
                return [], "JSON must be a list of questions or {questions: [...]}"
        except Exception as e:
            return [], str(e)

    def _validate_records(
        self,
        parse_result: Tuple[List[Dict[str, Any]], Optional[str]],
    ) -> DatasetValidationResult:
        rows, parse_error = parse_result
        errors: List[ValidationError] = []
        warnings: List[str] = []
        seen_ids: Dict[str, int] = {}
        duplicate_ids: List[str] = []

        if parse_error:
            return DatasetValidationResult(
                total_rows=0,
                valid_rows=0,
                invalid_rows=0,
                errors=[ValidationError(row=0, field="file", message=f"Parse error: {parse_error}")],
                warnings=[],
                preview=[],
                duplicate_ids=[],
            )

        for i, row in enumerate(rows):
            row_num = i + 1

            # Check required fields
            for field_name in REQUIRED_FIELDS:
                val = row.get(field_name, "").strip() if isinstance(row.get(field_name), str) else row.get(field_name)
                if not val:
                    errors.append(ValidationError(row=row_num, field=field_name, message=f"Required field '{field_name}' is missing or empty"))

            # Validate language
            lang = str(row.get("language", "")).strip().lower()
            if lang and lang not in VALID_LANGUAGES:
                errors.append(ValidationError(row=row_num, field="language", message=f"Invalid language '{lang}'. Must be one of: {sorted(VALID_LANGUAGES)}"))

            # Validate category
            cat = str(row.get("category", "")).strip().lower()
            if cat and cat not in VALID_CATEGORIES:
                errors.append(ValidationError(row=row_num, field="category", message=f"Invalid category '{cat}'. Must be one of: {sorted(VALID_CATEGORIES)}"))

            # Validate question_type if present
            qt = str(row.get("question_type", "")).strip().lower()
            if qt and qt not in VALID_QUESTION_TYPES:
                errors.append(ValidationError(row=row_num, field="question_type", message=f"Invalid question_type '{qt}'"))

            # Check for duplicate external IDs
            ext_id = str(row.get("external_question_id", "")).strip()
            if ext_id:
                if ext_id in seen_ids:
                    if ext_id not in duplicate_ids:
                        duplicate_ids.append(ext_id)
                    errors.append(ValidationError(row=row_num, field="external_question_id", message=f"Duplicate external_question_id: {ext_id} (first seen at row {seen_ids[ext_id]})"))
                else:
                    seen_ids[ext_id] = row_num

        # Unknown fields warning
        if rows:
            unknown_fields = set(rows[0].keys()) - ALL_FIELDS
            if unknown_fields:
                warnings.append(f"Unknown columns (will be ignored): {sorted(unknown_fields)}")

        invalid_rows = len({e.row for e in errors})
        valid_rows = len(rows) - invalid_rows

        return DatasetValidationResult(
            total_rows=len(rows),
            valid_rows=valid_rows,
            invalid_rows=invalid_rows,
            errors=errors,
            warnings=warnings,
            preview=rows[:5],
            duplicate_ids=duplicate_ids,
        )

    async def import_records(
        self,
        db: AsyncSession,
        dataset: Dataset,
        content: bytes,
        format: str,  # "csv" or "json"
        skip_invalid: bool = False,
    ) -> Tuple[int, int]:
        """Import validated records into the database. Returns (imported, skipped)."""
        if format == "csv":
            rows, parse_error = self._parse_csv(content)
        else:
            rows, parse_error = self._parse_json(content)

        if parse_error:
            raise ValueError(f"Failed to parse {format}: {parse_error}")

        # Validate first
        validation = self._validate_records((rows, None))
        invalid_row_nums = {e.row for e in validation.errors}

        imported = 0
        skipped = 0
        for i, row in enumerate(rows):
            row_num = i + 1
            if row_num in invalid_row_nums:
                if not skip_invalid:
                    raise ValueError(f"Row {row_num} has validation errors. Fix errors or use skip_invalid=True")
                skipped += 1
                continue

            # Parse acceptable_answers
            acceptable_answers_raw = row.get("acceptable_answers", "")
            acceptable_answers = None
            if acceptable_answers_raw:
                try:
                    acceptable_answers = json.loads(str(acceptable_answers_raw))
                    if not isinstance(acceptable_answers, list):
                        acceptable_answers = [str(acceptable_answers_raw)]
                except Exception:
                    # Try comma-separated
                    acceptable_answers = [a.strip() for a in str(acceptable_answers_raw).split("|") if a.strip()]

            question = Question(
                dataset_id=dataset.id,
                external_question_id=str(row.get("external_question_id", "")).strip() or None,
                comparison_group_id=str(row.get("comparison_group_id", "")).strip() or None,
                question_text=str(row.get("question_text", "")).strip(),
                language=Language(str(row.get("language", "")).strip().lower()),
                category=Category(str(row.get("category", "")).strip().lower()),
                context=str(row.get("context", "")).strip() or None,
                question_type=QuestionType(str(row.get("question_type", "factual")).strip().lower() or "factual"),
                reference_answer=str(row.get("reference_answer", "")).strip() or None,
                acceptable_answers=acceptable_answers,
                evaluation_rubric=str(row.get("evaluation_rubric", "")).strip() or None,
            )
            db.add(question)
            imported += 1

        dataset.row_count = imported
        dataset.source_format = format
        await db.flush()
        return imported, skipped

    def get_csv_template(self) -> str:
        """Return the CSV template for dataset upload."""
        fields = [
            "external_question_id", "comparison_group_id", "question_text",
            "language", "category", "context", "question_type",
            "reference_answer", "acceptable_answers", "evaluation_rubric"
        ]
        return ",".join(fields) + "\n"


dataset_service = DatasetService()
