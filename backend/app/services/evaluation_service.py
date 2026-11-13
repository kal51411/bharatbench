"""Evaluation service: auto-evaluation and human evaluation."""
import logging
import re
import unicodedata
from typing import Optional, List, Tuple, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from app.models.models import (
    ModelRun, Evaluation, BiasAnnotation, Question, User,
    CorrectnessLabel, ReviewStatus, EvaluationMethod, RunStatus,
    BiasCategory, BiasSeverity
)
from app.schemas.schemas import EvaluationCreate, EvaluationUpdate

logger = logging.getLogger(__name__)

EVAL_METHOD_VERSION = "1.0"
SCORE_MAP = {
    CorrectnessLabel.correct: 1.0,
    CorrectnessLabel.partially_correct: 0.5,
    CorrectnessLabel.incorrect: 0.0,
    CorrectnessLabel.unanswerable: None,
    CorrectnessLabel.needs_adjudication: None,
}


def normalize_text(text: str) -> str:
    """Safe Unicode normalization for multilingual text comparison."""
    if not text:
        return ""
    # Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)
    # Lowercase (works for Latin and Devanagari scripts)
    text = text.lower()
    # Remove punctuation except Devanagari-specific characters
    text = re.sub(r'[^\w\s\u0900-\u097F\u0980-\u09FF\u0B80-\u0BFF\u0C00-\u0C7F\u0C80-\u0CFF]', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


class EvaluationService:

    # ───── Auto-evaluation ─────

    async def auto_evaluate_run(
        self,
        db: AsyncSession,
        model_run: ModelRun,
    ) -> Optional[Evaluation]:
        """
        Apply automatic evaluation to a completed model run.
        Returns the Evaluation if successful, None if the run is not evaluable.
        """
        if model_run.response_status != RunStatus.completed:
            return None
        if not model_run.response_text:
            return None

        # Load the question
        q_result = await db.execute(select(Question).where(Question.id == model_run.question_id))
        question = q_result.scalar_one_or_none()
        if not question:
            return None

        # Check if auto-evaluation already exists for this run
        existing = await db.execute(
            select(Evaluation).where(
                and_(
                    Evaluation.model_run_id == model_run.id,
                    Evaluation.evaluator_id == None,  # noqa: E711
                )
            )
        )
        if existing.scalar_one_or_none():
            return None  # Already auto-evaluated

        label, score, method = self._score_response(
            response=model_run.response_text,
            reference_answer=question.reference_answer,
            acceptable_answers=question.acceptable_answers,
        )

        evaluation = Evaluation(
            model_run_id=model_run.id,
            evaluator_id=None,  # auto = no human
            evaluation_method=method,
            evaluation_method_version=EVAL_METHOD_VERSION,
            correctness_label=label,
            correctness_score=score,
            review_status=ReviewStatus.reviewed,
        )
        db.add(evaluation)
        await db.flush()
        return evaluation

    def _score_response(
        self,
        response: str,
        reference_answer: Optional[str],
        acceptable_answers: Optional[List[str]],
    ) -> Tuple[CorrectnessLabel, Optional[float], EvaluationMethod]:
        """
        Score a response using deterministic methods.
        Returns (label, score, method).
        """
        if not reference_answer and not acceptable_answers:
            return CorrectnessLabel.needs_adjudication, None, EvaluationMethod.exact_match

        norm_response = normalize_text(response)

        # Try exact match with reference answer
        if reference_answer:
            norm_ref = normalize_text(reference_answer)
            if norm_response == norm_ref:
                return CorrectnessLabel.correct, 1.0, EvaluationMethod.exact_match

        # Try accepted answers matching
        if acceptable_answers:
            for ans in acceptable_answers:
                norm_ans = normalize_text(str(ans))
                if norm_ans and norm_ans in norm_response:
                    return CorrectnessLabel.correct, 1.0, EvaluationMethod.accepted_answer_match

        # Keyword matching: check if reference answer key terms appear in response
        if reference_answer:
            keywords = [w for w in normalize_text(reference_answer).split() if len(w) > 3]
            if keywords:
                matching = sum(1 for kw in keywords if kw in norm_response)
                ratio = matching / len(keywords)
                if ratio >= 0.8:
                    return CorrectnessLabel.correct, 1.0, EvaluationMethod.keyword_match
                elif ratio >= 0.4:
                    return CorrectnessLabel.partially_correct, 0.5, EvaluationMethod.keyword_match
                else:
                    return CorrectnessLabel.incorrect, 0.0, EvaluationMethod.keyword_match

        return CorrectnessLabel.needs_adjudication, None, EvaluationMethod.exact_match

    # ───── Human evaluation ─────

    async def create_human_evaluation(
        self,
        db: AsyncSession,
        eval_in: EvaluationCreate,
        evaluator_id: str,
    ) -> Evaluation:
        """Submit a human evaluation for a model run."""
        # Check the model run exists
        run_result = await db.execute(select(ModelRun).where(ModelRun.id == eval_in.model_run_id))
        model_run = run_result.scalar_one_or_none()
        if not model_run:
            raise ValueError(f"ModelRun {eval_in.model_run_id} not found")

        # Check for existing evaluation by this evaluator
        existing = await db.execute(
            select(Evaluation).where(
                and_(
                    Evaluation.model_run_id == eval_in.model_run_id,
                    Evaluation.evaluator_id == evaluator_id,
                )
            )
        )
        existing_eval = existing.scalar_one_or_none()
        if existing_eval:
            raise ValueError("You have already submitted an evaluation for this model run. Use PATCH to update it.")

        evaluation = Evaluation(
            model_run_id=eval_in.model_run_id,
            evaluator_id=evaluator_id,
            evaluation_method=EvaluationMethod.human,
            evaluation_method_version=EVAL_METHOD_VERSION,
            correctness_label=eval_in.correctness_label,
            correctness_score=eval_in.correctness_score,
            confidence=eval_in.confidence,
            review_status=ReviewStatus.reviewed,
            notes=eval_in.notes,
        )
        db.add(evaluation)
        await db.flush()

        # Create bias annotation if provided
        if eval_in.is_bias_flagged is not None:
            bias = BiasAnnotation(
                evaluation_id=evaluation.id,
                is_bias_flagged=eval_in.is_bias_flagged,
                bias_category=eval_in.bias_category,
                severity=eval_in.bias_severity,
                evidence=eval_in.bias_evidence,
                reviewer_notes=eval_in.bias_reviewer_notes,
            )
            db.add(bias)
            await db.flush()

        await db.refresh(evaluation)
        return evaluation

    async def update_evaluation(
        self,
        db: AsyncSession,
        evaluation_id: str,
        update_in: EvaluationUpdate,
        updated_by_id: str,
    ) -> Evaluation:
        """Update an existing evaluation (creates audit log)."""
        from app.models.models import EvaluationAuditLog
        result = await db.execute(select(Evaluation).where(Evaluation.id == evaluation_id))
        evaluation = result.scalar_one_or_none()
        if not evaluation:
            raise ValueError(f"Evaluation {evaluation_id} not found")

        updates = update_in.model_dump(exclude_none=True)
        for field, new_val in updates.items():
            old_val = getattr(evaluation, field)
            if str(old_val) != str(new_val):
                audit = EvaluationAuditLog(
                    evaluation_id=evaluation_id,
                    changed_by_id=updated_by_id,
                    field_changed=field,
                    old_value=str(old_val) if old_val is not None else None,
                    new_value=str(new_val) if new_val is not None else None,
                )
                db.add(audit)
            setattr(evaluation, field, new_val)

        await db.flush()
        await db.refresh(evaluation)
        return evaluation

    async def get_evaluation(self, db: AsyncSession, evaluation_id: str) -> Optional[Evaluation]:
        result = await db.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        return result.scalar_one_or_none()

    async def list_evaluations(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 20,
        experiment_id: Optional[str] = None,
        review_status: Optional[str] = None,
    ) -> Tuple[List[Evaluation], int]:
        q = select(Evaluation)
        count_q = select(func.count(Evaluation.id))

        if experiment_id:
            q = q.join(ModelRun).where(ModelRun.experiment_id == experiment_id)
            count_q = count_q.join(ModelRun).where(ModelRun.experiment_id == experiment_id)
        if review_status:
            q = q.where(Evaluation.review_status == review_status)
            count_q = count_q.where(Evaluation.review_status == review_status)

        total = (await db.execute(count_q)).scalar_one()
        result = await db.execute(q.order_by(Evaluation.created_at.desc()).offset(skip).limit(limit))
        return result.scalars().all(), total

    async def get_pending_review_count(self, db: AsyncSession) -> int:
        """Count model runs that have no human evaluation yet."""
        result = await db.execute(
            select(func.count(ModelRun.id)).where(
                and_(
                    ModelRun.response_status == RunStatus.completed,
                    ~ModelRun.id.in_(
                        select(Evaluation.model_run_id).where(
                            Evaluation.evaluator_id != None  # noqa: E711
                        )
                    )
                )
            )
        )
        return result.scalar_one()


evaluation_service = EvaluationService()
