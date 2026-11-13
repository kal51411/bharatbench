"""Export routes for research data."""
import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.models.models import User, ModelRun, Evaluation, Question, Experiment, BiasAnnotation
from app.api.v1.deps import get_current_user

router = APIRouter(prefix="/exports", tags=["exports"])


async def _get_experiment_data(db: AsyncSession, experiment_id: str):
    experiment = await db.execute(select(Experiment).where(Experiment.id == experiment_id))
    exp = experiment.scalar_one_or_none()
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return exp


@router.get("/experiments/{experiment_id}/csv")
async def export_experiment_csv(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export experiment results as CSV. DEMO responses are labelled."""
    exp = await _get_experiment_data(db, experiment_id)

    # Load runs with questions
    runs_result = await db.execute(
        select(ModelRun).where(ModelRun.experiment_id == experiment_id)
    )
    runs = runs_result.scalars().all()

    # Load questions
    q_ids = {r.question_id for r in runs}
    if q_ids:
        qs_result = await db.execute(select(Question).where(Question.id.in_(q_ids)))
        questions = {q.id: q for q in qs_result.scalars().all()}
    else:
        questions = {}

    # Load evaluations
    eval_result = await db.execute(
        select(Evaluation).join(ModelRun).where(ModelRun.experiment_id == experiment_id)
    )
    evaluations = {e.model_run_id: e for e in eval_result.scalars().all()}

    output = io.StringIO()
    fieldnames = [
        "run_id", "experiment_id", "experiment_name", "question_id", "external_question_id",
        "comparison_group_id", "question_text", "language", "category", "reference_answer",
        "provider", "model_name", "model_version", "prompt", "response_text", "response_status",
        "latency_ms", "input_tokens", "output_tokens", "is_demo",
        "correctness_label", "correctness_score", "evaluation_method", "evaluator_id",
        "bias_flagged", "bias_category", "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for run in runs:
        q = questions.get(run.question_id)
        ev = evaluations.get(run.id)
        bias_flagged = ""
        bias_cat = ""
        if ev:
            bias_anns = await db.execute(
                select(BiasAnnotation).where(BiasAnnotation.evaluation_id == ev.id)
            )
            bias_list = bias_anns.scalars().all()
            if bias_list:
                bias_flagged = str(bias_list[0].is_bias_flagged)
                bias_cat = bias_list[0].bias_category.value if bias_list[0].bias_category else ""

        writer.writerow({
            "run_id": run.id,
            "experiment_id": experiment_id,
            "experiment_name": exp.experiment_name,
            "question_id": run.question_id,
            "external_question_id": q.external_question_id if q else "",
            "comparison_group_id": q.comparison_group_id if q else "",
            "question_text": q.question_text if q else "",
            "language": q.language.value if q else "",
            "category": q.category.value if q else "",
            "reference_answer": q.reference_answer if q else "",
            "provider": run.provider,
            "model_name": run.model_name,
            "model_version": run.model_version or "",
            "prompt": run.prompt or "",
            "response_text": ("[DEMO] " if run.is_demo else "") + (run.response_text or ""),
            "response_status": run.response_status.value,
            "latency_ms": run.latency_ms or "",
            "input_tokens": run.input_tokens or "",
            "output_tokens": run.output_tokens or "",
            "is_demo": run.is_demo,
            "correctness_label": ev.correctness_label.value if ev and ev.correctness_label else "",
            "correctness_score": ev.correctness_score if ev else "",
            "evaluation_method": ev.evaluation_method.value if ev else "",
            "evaluator_id": ev.evaluator_id if ev else "",
            "bias_flagged": bias_flagged,
            "bias_category": bias_cat,
            "created_at": run.created_at.isoformat(),
        })

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=experiment_{experiment_id}.csv"},
    )


@router.get("/experiments/{experiment_id}/json")
async def export_experiment_json(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export experiment results as JSON."""
    exp = await _get_experiment_data(db, experiment_id)

    runs_result = await db.execute(
        select(ModelRun).where(ModelRun.experiment_id == experiment_id)
    )
    runs = runs_result.scalars().all()

    q_ids = {r.question_id for r in runs}
    if q_ids:
        qs_result = await db.execute(select(Question).where(Question.id.in_(q_ids)))
        questions = {q.id: q for q in qs_result.scalars().all()}
    else:
        questions = {}

    eval_result = await db.execute(
        select(Evaluation).join(ModelRun).where(ModelRun.experiment_id == experiment_id)
    )
    evaluations = {e.model_run_id: e for e in eval_result.scalars().all()}

    export_data = {
        "export_metadata": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "experiment_id": experiment_id,
            "experiment_name": exp.experiment_name,
            "note": "DEMO responses are from MockLLMProvider and are NOT actual model outputs. Do not use for research findings.",
        },
        "experiment": {
            "id": exp.id,
            "name": exp.experiment_name,
            "description": exp.description,
            "dataset_version_id": exp.dataset_version_id,
            "status": exp.status.value,
            "configuration": exp.configuration,
            "created_at": exp.created_at.isoformat(),
        },
        "runs": []
    }

    for run in runs:
        q = questions.get(run.question_id)
        ev = evaluations.get(run.id)
        run_data = {
            "run_id": run.id,
            "question_id": run.question_id,
            "question_text": q.question_text if q else None,
            "language": q.language.value if q else None,
            "category": q.category.value if q else None,
            "reference_answer": q.reference_answer if q else None,
            "provider": run.provider,
            "model_name": run.model_name,
            "model_version": run.model_version,
            "prompt": run.prompt,
            "response_text": run.response_text,
            "is_demo": run.is_demo,
            "response_status": run.response_status.value,
            "latency_ms": run.latency_ms,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "evaluation": None,
        }
        if ev:
            run_data["evaluation"] = {
                "correctness_label": ev.correctness_label.value if ev.correctness_label else None,
                "correctness_score": ev.correctness_score,
                "evaluation_method": ev.evaluation_method.value,
                "review_status": ev.review_status.value,
            }
        export_data["runs"].append(run_data)

    return StreamingResponse(
        io.BytesIO(json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=experiment_{experiment_id}.json"},
    )


@router.get("/experiments/{experiment_id}/report")
async def export_experiment_report(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export a markdown research summary report."""
    from app.services.analytics_service import analytics_service
    exp = await _get_experiment_data(db, experiment_id)
    model_accuracy = await analytics_service.get_model_accuracy(db, experiment_id)
    language_accuracy = await analytics_service.get_language_accuracy(db, experiment_id)
    bias = await analytics_service.get_bias_analysis(db, experiment_id)

    has_demo = False
    runs_result = await db.execute(
        select(ModelRun).where(ModelRun.experiment_id == experiment_id)
    )
    runs = runs_result.scalars().all()
    if any(r.is_demo for r in runs):
        has_demo = True

    report = f"""# BharatBench Research Report

## Experiment: {exp.experiment_name}

Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

{"⚠️ **NOTICE: This experiment contains DEMO responses from MockLLMProvider. DEMO results are NOT actual model outputs and must NOT be used as research findings.**" if has_demo else ""}

---

## Experiment Details

| Field | Value |
|-------|-------|
| ID | {exp.id} |
| Status | {exp.status.value} |
| Dataset | {exp.dataset_version_id} |
| Created | {exp.created_at.strftime('%Y-%m-%d %H:%M UTC')} |

### Configuration

```json
{json.dumps(exp.configuration, indent=2) if exp.configuration else 'N/A'}
```

---

## Model Accuracy Results

| Provider | Model | Total Evaluated | Full-Credit Accuracy | Partial-Credit Score | Avg Latency (ms) |
|----------|-------|-----------------|---------------------|---------------------|-----------------|
"""
    for m in model_accuracy:
        report += f"| {m.provider} | {m.model_name} | {m.total_evaluated} | {m.full_credit_accuracy:.1f}% | {m.partial_credit_score:.1f}% | {m.avg_latency_ms or 'N/A'} |\n"

    report += f"""
---

## Language Performance

| Language | Total Evaluated | Full-Credit Accuracy | vs. English |
|----------|-----------------|---------------------|-------------|
"""
    for l in language_accuracy:
        vs_en = f"{l.vs_english_diff:+.1f}%" if l.vs_english_diff is not None else "baseline"
        report += f"| {l.language} | {l.total_evaluated} | {l.full_credit_accuracy:.1f}% | {vs_en} |\n"

    report += f"""
---

## Bias Analysis

- **Total Reviewed**: {bias.total_reviewed}
- **Bias Flagged**: {bias.bias_flagged} ({bias.bias_rate:.1f}%)
- **Not Flagged**: {bias.not_flagged}
- **Pending Review**: {bias.pending}

### Bias by Category

| Category | Count |
|----------|-------|
"""
    for cat, count in bias.by_category.items():
        report += f"| {cat} | {count} |\n"

    report += """
---

## Methodology Notes

- **Full-Credit Accuracy**: Correct / Eligible Evaluated × 100
- **Partial-Credit Score**: (Correct×1.0 + Partial×0.5) / Eligible × 100
- **Eligible**: Responses labelled correct, partially_correct, or incorrect
- **Bias Rate**: Bias-flagged / Reviewed bias annotations × 100
- **Evaluation Methods**: Exact match, accepted-answer match, keyword matching, human review
- Hosted model providers may update their models; exact reproduction may not be possible.

---

*BharatBench v0.1.0 — Research Platform for Indian LLM Evaluation*
"""

    return StreamingResponse(
        io.BytesIO(report.encode("utf-8")),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=report_{experiment_id}.md"},
    )
