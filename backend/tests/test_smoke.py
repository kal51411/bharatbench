import asyncio

import pytest

from app.models.models import ExperimentModel, Question, Language, Category
from app.services.dataset_service import DatasetService
from app.services.evaluation_service import normalize_text
from app.services.experiment_service import ExperimentService


def test_unicode_normalization():
    assert normalize_text("  नमस्ते, भारत! ") == "नमस्ते भारत"


def test_csv_validation_accepts_unicode():
    result = DatasetService().validate_csv(
        "question_text,language,category,reference_answer\nभारत की राजधानी क्या है?,hindi,geography,नई दिल्ली\n".encode()
    )
    assert result.total_rows == 1
    assert result.valid_rows == 1
    assert result.invalid_rows == 0


@pytest.mark.parametrize("payload", [
    b"question_text,language,category\nMissing language,,science\n",
    b"question_text,language,category\nBad language,kannada,science\n",
])
def test_csv_validation_rejects_invalid_rows(payload):
    result = DatasetService().validate_csv(payload)
    assert result.invalid_rows == 1
    assert result.errors


@pytest.mark.asyncio
async def test_mock_workers_run_concurrently_without_database_access():
    service = ExperimentService()
    question = Question(
        id="question-1",
        question_text="What is India's capital?",
        language=Language.english,
        category=Category.geography,
    )
    model = ExperimentModel(id="model-1", provider="mock", model_name="mock-demo-v1")
    event = asyncio.Event()
    semaphore = asyncio.Semaphore(2)
    results = await asyncio.gather(
        service._worker(question, model, {}, event, semaphore),
        service._worker(question, model, {}, event, semaphore),
    )
    assert all(result.response and result.response.is_demo for result in results)
