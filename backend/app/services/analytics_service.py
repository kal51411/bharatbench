"""Analytics engine: all research metrics computed server-side."""
import logging
import math
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, case, text

from app.models.models import (
    ModelRun, Evaluation, BiasAnnotation, Question, Dataset, Experiment,
    RunStatus, CorrectnessLabel, ReviewStatus, Language, Category, User
)
from app.schemas.schemas import (
    ModelAccuracy, LanguageAccuracy, CategoryAccuracy,
    BiasAnalysis, OverviewStats, ConsistencyResult
)

logger = logging.getLogger(__name__)

SCORE_MAP = {
    "correct": 1.0,
    "partially_correct": 0.5,
    "incorrect": 0.0,
}

# Labels that count toward accuracy denominator
ACCURACY_ELIGIBLE = {"correct", "partially_correct", "incorrect"}


def confidence_interval_95(p: float, n: int) -> Tuple[float, float]:
    """Wilson score 95% CI for a proportion p from n trials."""
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    center = (p + z*z / (2*n)) / (1 + z*z/n)
    margin = (z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)
    return max(0.0, round(center - margin, 4)), min(1.0, round(center + margin, 4))


class AnalyticsService:

    async def get_overview(self, db: AsyncSession) -> OverviewStats:
        """Compute platform-wide overview statistics."""
        total_datasets = (await db.execute(select(func.count(Dataset.id)))).scalar_one()
        total_questions = (await db.execute(select(func.count(Question.id)))).scalar_one()
        total_experiments = (await db.execute(select(func.count(Experiment.id)))).scalar_one()
        total_runs = (await db.execute(select(func.count(ModelRun.id)))).scalar_one()
        completed_runs = (await db.execute(
            select(func.count(ModelRun.id)).where(ModelRun.response_status == RunStatus.completed)
        )).scalar_one()

        # Pending human evaluations
        from app.services.evaluation_service import evaluation_service
        pending_evals = await evaluation_service.get_pending_review_count(db)

        # Overall accuracy from reviewed human evaluations
        overall_accuracy = await self._compute_overall_accuracy(db)

        # Reviewed bias rate
        bias_rate = await self._compute_bias_rate(db)

        # Configured providers
        from app.services.providers.base import provider_registry
        configured = [p.provider_name for p in provider_registry.list_providers() if p.is_configured()]

        return OverviewStats(
            total_datasets=total_datasets,
            total_questions=total_questions,
            total_experiments=total_experiments,
            total_model_runs=total_runs,
            completed_runs=completed_runs,
            pending_evaluations=pending_evals,
            overall_accuracy=overall_accuracy,
            reviewed_bias_rate=bias_rate,
            configured_providers=configured,
        )

    async def get_model_accuracy(
        self,
        db: AsyncSession,
        experiment_id: Optional[str] = None,
    ) -> List[ModelAccuracy]:
        """Accuracy broken down by model, using only reviewed evaluations."""
        q = (
            select(
                ModelRun.provider,
                ModelRun.model_name,
                Evaluation.correctness_label,
                func.count(Evaluation.id).label("cnt"),
                func.avg(ModelRun.latency_ms).label("avg_latency"),
            )
            .join(Evaluation, Evaluation.model_run_id == ModelRun.id)
            .where(Evaluation.review_status == ReviewStatus.reviewed)
            .where(Evaluation.correctness_label != None)  # noqa: E711
        )
        if experiment_id:
            q = q.where(ModelRun.experiment_id == experiment_id)
        q = q.group_by(ModelRun.provider, ModelRun.model_name, Evaluation.correctness_label)

        result = await db.execute(q)
        rows = result.all()

        # Aggregate by model
        model_data: Dict[Tuple[str, str], Dict] = {}
        for provider, model_name, label, cnt, avg_lat in rows:
            key = (provider, model_name)
            if key not in model_data:
                model_data[key] = {
                    "provider": provider,
                    "model_name": model_name,
                    "counts": {},
                    "avg_latency": avg_lat,
                }
            if label:
                model_data[key]["counts"][label.value if hasattr(label, 'value') else str(label)] = cnt

        results = []
        for key, data in model_data.items():
            counts = data["counts"]
            correct = counts.get("correct", 0)
            partial = counts.get("partially_correct", 0)
            incorrect = counts.get("incorrect", 0)
            eligible = correct + partial + incorrect

            full_acc = round(correct / eligible * 100, 2) if eligible > 0 else 0.0
            partial_score = round((correct * 1.0 + partial * 0.5) / eligible * 100, 2) if eligible > 0 else 0.0

            # Bias rate for this model
            bias_rate = await self._compute_bias_rate(db, model_name=data["model_name"])

            results.append(ModelAccuracy(
                provider=data["provider"],
                model_name=data["model_name"],
                total_evaluated=eligible,
                correct=correct,
                partially_correct=partial,
                incorrect=incorrect,
                full_credit_accuracy=full_acc,
                partial_credit_score=partial_score,
                avg_latency_ms=round(data["avg_latency"], 1) if data["avg_latency"] else None,
                bias_rate=bias_rate,
            ))

        return sorted(results, key=lambda x: x.full_credit_accuracy, reverse=True)

    async def get_language_accuracy(
        self,
        db: AsyncSession,
        experiment_id: Optional[str] = None,
    ) -> List[LanguageAccuracy]:
        """Accuracy per language."""
        q = (
            select(
                Question.language,
                Evaluation.correctness_label,
                func.count(Evaluation.id).label("cnt"),
            )
            .join(ModelRun, ModelRun.id == Evaluation.model_run_id)
            .join(Question, Question.id == ModelRun.question_id)
            .where(Evaluation.review_status == ReviewStatus.reviewed)
            .where(Evaluation.correctness_label != None)  # noqa: E711
        )
        if experiment_id:
            q = q.where(ModelRun.experiment_id == experiment_id)
        q = q.group_by(Question.language, Evaluation.correctness_label)

        result = await db.execute(q)
        rows = result.all()

        lang_data: Dict[str, Dict] = {}
        for lang, label, cnt in rows:
            lang_val = lang.value if hasattr(lang, 'value') else str(lang)
            if lang_val not in lang_data:
                lang_data[lang_val] = {}
            label_val = label.value if hasattr(label, 'value') else str(label)
            lang_data[lang_val][label_val] = cnt

        # Get English accuracy for comparison
        english_data = lang_data.get("english", {})
        en_eligible = sum(english_data.get(l, 0) for l in ["correct", "partially_correct", "incorrect"])
        en_accuracy = (english_data.get("correct", 0) / en_eligible * 100) if en_eligible > 0 else None

        results = []
        for lang_val, counts in lang_data.items():
            correct = counts.get("correct", 0)
            partial = counts.get("partially_correct", 0)
            incorrect = counts.get("incorrect", 0)
            eligible = correct + partial + incorrect

            full_acc = round(correct / eligible * 100, 2) if eligible > 0 else 0.0
            partial_score = round((correct * 1.0 + partial * 0.5) / eligible * 100, 2) if eligible > 0 else 0.0

            vs_english = None
            if en_accuracy is not None and lang_val != "english":
                vs_english = round(full_acc - en_accuracy, 2)

            results.append(LanguageAccuracy(
                language=lang_val,
                total_evaluated=eligible,
                full_credit_accuracy=full_acc,
                partial_credit_score=partial_score,
                vs_english_diff=vs_english,
            ))

        return sorted(results, key=lambda x: x.full_credit_accuracy, reverse=True)

    async def get_category_accuracy(
        self,
        db: AsyncSession,
        experiment_id: Optional[str] = None,
    ) -> List[CategoryAccuracy]:
        """Accuracy per category."""
        q = (
            select(
                Question.category,
                Evaluation.correctness_label,
                func.count(Evaluation.id).label("cnt"),
            )
            .join(ModelRun, ModelRun.id == Evaluation.model_run_id)
            .join(Question, Question.id == ModelRun.question_id)
            .where(Evaluation.review_status == ReviewStatus.reviewed)
            .where(Evaluation.correctness_label != None)  # noqa: E711
        )
        if experiment_id:
            q = q.where(ModelRun.experiment_id == experiment_id)
        q = q.group_by(Question.category, Evaluation.correctness_label)

        result = await db.execute(q)
        rows = result.all()

        cat_data: Dict[str, Dict] = {}
        for cat, label, cnt in rows:
            cat_val = cat.value if hasattr(cat, 'value') else str(cat)
            if cat_val not in cat_data:
                cat_data[cat_val] = {}
            label_val = label.value if hasattr(label, 'value') else str(label)
            cat_data[cat_val][label_val] = cnt

        results = []
        for cat_val, counts in cat_data.items():
            correct = counts.get("correct", 0)
            partial = counts.get("partially_correct", 0)
            incorrect = counts.get("incorrect", 0)
            eligible = correct + partial + incorrect

            full_acc = round(correct / eligible * 100, 2) if eligible > 0 else 0.0
            partial_score = round((correct * 1.0 + partial * 0.5) / eligible * 100, 2) if eligible > 0 else 0.0

            results.append(CategoryAccuracy(
                category=cat_val,
                total_evaluated=eligible,
                full_credit_accuracy=full_acc,
                partial_credit_score=partial_score,
            ))

        return sorted(results, key=lambda x: x.full_credit_accuracy, reverse=True)

    async def get_bias_analysis(
        self,
        db: AsyncSession,
        experiment_id: Optional[str] = None,
    ) -> BiasAnalysis:
        """Compute bias analysis from reviewed bias annotations."""
        # Base query - only use REVIEWED bias annotations
        base_q = (
            select(BiasAnnotation)
            .join(Evaluation, Evaluation.id == BiasAnnotation.evaluation_id)
            .where(BiasAnnotation.is_bias_flagged != None)  # noqa: E711 - Only reviewed ones
        )
        if experiment_id:
            base_q = base_q.join(ModelRun, ModelRun.id == Evaluation.model_run_id).where(
                ModelRun.experiment_id == experiment_id
            )

        result = await db.execute(base_q)
        annotations = result.scalars().all()

        total_reviewed = len(annotations)
        bias_flagged = sum(1 for a in annotations if a.is_bias_flagged)
        not_flagged = sum(1 for a in annotations if a.is_bias_flagged is False)
        pending = total_reviewed - bias_flagged - not_flagged

        bias_rate = round(bias_flagged / total_reviewed * 100, 2) if total_reviewed > 0 else 0.0

        # Breakdown by category
        by_category: Dict[str, int] = {}
        for ann in annotations:
            if ann.is_bias_flagged and ann.bias_category:
                cat = ann.bias_category.value if hasattr(ann.bias_category, 'value') else str(ann.bias_category)
                by_category[cat] = by_category.get(cat, 0) + 1

        # By model
        by_model_q = (
            select(
                ModelRun.model_name,
                func.count(BiasAnnotation.id).label("total"),
                func.sum(case((BiasAnnotation.is_bias_flagged == True, 1), else_=0)).label("flagged"),
            )
            .join(Evaluation, Evaluation.id == BiasAnnotation.evaluation_id)
            .join(ModelRun, ModelRun.id == Evaluation.model_run_id)
            .where(BiasAnnotation.is_bias_flagged != None)  # noqa: E711
            .group_by(ModelRun.model_name)
        )
        if experiment_id:
            by_model_q = by_model_q.where(ModelRun.experiment_id == experiment_id)
        by_model_result = await db.execute(by_model_q)
        by_model = [
            {"model_name": row[0], "total": row[1], "flagged": int(row[2] or 0),
             "rate": round(int(row[2] or 0) / row[1] * 100, 2) if row[1] > 0 else 0.0}
            for row in by_model_result.all()
        ]

        # By language
        by_lang_q = (
            select(
                Question.language,
                func.count(BiasAnnotation.id).label("total"),
                func.sum(case((BiasAnnotation.is_bias_flagged == True, 1), else_=0)).label("flagged"),
            )
            .join(Evaluation, Evaluation.id == BiasAnnotation.evaluation_id)
            .join(ModelRun, ModelRun.id == Evaluation.model_run_id)
            .join(Question, Question.id == ModelRun.question_id)
            .where(BiasAnnotation.is_bias_flagged != None)  # noqa: E711
            .group_by(Question.language)
        )
        if experiment_id:
            by_lang_q = by_lang_q.where(ModelRun.experiment_id == experiment_id)
        by_lang_result = await db.execute(by_lang_q)
        by_language = [
            {"language": row[0].value if hasattr(row[0], 'value') else str(row[0]),
             "total": row[1], "flagged": int(row[2] or 0),
             "rate": round(int(row[2] or 0) / row[1] * 100, 2) if row[1] > 0 else 0.0}
            for row in by_lang_result.all()
        ]

        return BiasAnalysis(
            total_reviewed=total_reviewed,
            bias_flagged=bias_flagged,
            not_flagged=not_flagged,
            pending=pending,
            bias_rate=bias_rate,
            by_category=by_category,
            by_model=by_model,
            by_language=by_language,
        )

    async def get_consistency_analysis(
        self,
        db: AsyncSession,
        experiment_id: Optional[str] = None,
    ) -> List[ConsistencyResult]:
        """
        Cross-language consistency: compare model performance on equivalent questions
        grouped by comparison_group_id.
        """
        q = (
            select(
                Question.comparison_group_id,
                Question.language,
                ModelRun.model_name,
                Evaluation.correctness_score,
            )
            .join(ModelRun, ModelRun.id == Evaluation.model_run_id)
            .join(Question, Question.id == ModelRun.question_id)
            .where(Question.comparison_group_id != None)  # noqa: E711
            .where(Evaluation.review_status == ReviewStatus.reviewed)
            .where(Evaluation.correctness_label.in_([
                CorrectnessLabel.correct,
                CorrectnessLabel.partially_correct,
                CorrectnessLabel.incorrect,
            ]))
        )
        if experiment_id:
            q = q.where(ModelRun.experiment_id == experiment_id)

        result = await db.execute(q)
        rows = result.all()

        # Aggregate by (group_id, model_name)
        groups: Dict[Tuple[str, str], Dict] = {}
        for group_id, language, model_name, score in rows:
            lang_val = language.value if hasattr(language, 'value') else str(language)
            key = (group_id, model_name)
            if key not in groups:
                groups[key] = {"languages": [], "scores": {}}
            if lang_val not in groups[key]["languages"]:
                groups[key]["languages"].append(lang_val)
            groups[key]["scores"][lang_val] = score

        results = []
        for (group_id, model_name), data in groups.items():
            scores = data["scores"]
            langs = data["languages"]
            # Consistency: all scores within 0.5 of each other (within full/partial range)
            score_values = [v for v in scores.values() if v is not None]
            consistent = None
            if len(score_values) >= 2:
                consistent = max(score_values) - min(score_values) <= 0.3

            results.append(ConsistencyResult(
                comparison_group_id=group_id,
                languages_present=langs,
                model_name=model_name,
                consistent=consistent,
                scores=scores,
            ))

        return results

    async def _compute_overall_accuracy(self, db: AsyncSession) -> Optional[float]:
        result = await db.execute(
            select(
                func.count(Evaluation.id),
                func.sum(case(
                    (Evaluation.correctness_label == CorrectnessLabel.correct, 1),
                    else_=0
                ))
            )
            .where(Evaluation.review_status == ReviewStatus.reviewed)
            .where(Evaluation.correctness_label.in_([
                CorrectnessLabel.correct,
                CorrectnessLabel.partially_correct,
                CorrectnessLabel.incorrect,
            ]))
        )
        total, correct = result.one()
        if not total:
            return None
        return round((correct or 0) / total * 100, 2)

    async def _compute_bias_rate(
        self,
        db: AsyncSession,
        model_name: Optional[str] = None,
    ) -> Optional[float]:
        q = (
            select(
                func.count(BiasAnnotation.id),
                func.sum(case((BiasAnnotation.is_bias_flagged == True, 1), else_=0))
            )
            .where(BiasAnnotation.is_bias_flagged != None)  # noqa: E711
        )
        if model_name:
            q = q.join(Evaluation, Evaluation.id == BiasAnnotation.evaluation_id)
            q = q.join(ModelRun, ModelRun.id == Evaluation.model_run_id)
            q = q.where(ModelRun.model_name == model_name)
        result = await db.execute(q)
        total, flagged = result.one()
        if not total:
            return None
        return round((flagged or 0) / total * 100, 2)


analytics_service = AnalyticsService()
