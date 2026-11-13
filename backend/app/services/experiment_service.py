"""Experiment service: creation, execution, progress tracking."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, and_

from app.models.models import (
    Experiment, ExperimentModel, ExperimentEvent, ModelRun, Question,
    ExperimentStatus, RunStatus, Dataset
)
from app.schemas.schemas import ExperimentCreate, ExperimentProgress
from app.services.providers.base import provider_registry, LLMRequest

logger = logging.getLogger(__name__)


class ExperimentService:

    async def create_experiment(
        self,
        db: AsyncSession,
        exp_in: ExperimentCreate,
        created_by_id: str,
    ) -> Experiment:
        """Create an experiment and its model selections."""
        config_dict = exp_in.configuration.model_dump()

        experiment = Experiment(
            experiment_name=exp_in.experiment_name,
            description=exp_in.description,
            dataset_version_id=exp_in.dataset_version_id,
            status=ExperimentStatus.pending,
            configuration=config_dict,
            created_by_id=created_by_id,
        )
        db.add(experiment)
        await db.flush()

        # Add selected models
        for model_sel in exp_in.models:
            em = ExperimentModel(
                experiment_id=experiment.id,
                provider=model_sel.provider,
                model_name=model_sel.model_name,
                model_config=model_sel.model_config_extra,
            )
            db.add(em)

        # Create initial event
        event = ExperimentEvent(
            experiment_id=experiment.id,
            event_type="created",
            message=f"Experiment '{exp_in.experiment_name}' created.",
        )
        db.add(event)
        await db.flush()
        await db.refresh(experiment)
        return experiment

    async def get_experiment(self, db: AsyncSession, experiment_id: str) -> Optional[Experiment]:
        result = await db.execute(
            select(Experiment).where(Experiment.id == experiment_id)
        )
        return result.scalar_one_or_none()

    async def list_experiments(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Experiment], int]:
        count_result = await db.execute(select(func.count(Experiment.id)))
        total = count_result.scalar_one()
        result = await db.execute(
            select(Experiment).order_by(Experiment.created_at.desc()).offset(skip).limit(limit)
        )
        return result.scalars().all(), total

    async def get_progress(self, db: AsyncSession, experiment_id: str) -> ExperimentProgress:
        """Get real-time progress of an experiment."""
        experiment = await self.get_experiment(db, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        # Count runs by status
        result = await db.execute(
            select(ModelRun.response_status, func.count(ModelRun.id))
            .where(ModelRun.experiment_id == experiment_id)
            .group_by(ModelRun.response_status)
        )
        status_counts = {status: count for status, count in result.all()}

        total_runs = sum(status_counts.values())
        completed = status_counts.get(RunStatus.completed, 0)
        failed = status_counts.get(RunStatus.failed, 0)
        pending = status_counts.get(RunStatus.pending, 0) + status_counts.get(RunStatus.running, 0)

        # Count questions in dataset
        dataset_id = experiment.dataset_version_id
        config = experiment.configuration or {}
        lang_filter = config.get("language_filter")
        cat_filter = config.get("category_filter")

        q = select(func.count(Question.id)).where(Question.dataset_id == dataset_id)
        if lang_filter:
            q = q.where(Question.language.in_(lang_filter))
        if cat_filter:
            q = q.where(Question.category.in_(cat_filter))
        q_result = await db.execute(q)
        total_questions = q_result.scalar_one()

        progress = 0.0
        if total_runs > 0:
            progress = round((completed + failed) / total_runs * 100, 1)

        return ExperimentProgress(
            experiment_id=experiment_id,
            status=experiment.status,
            total_questions=total_questions,
            total_runs=total_runs,
            completed_runs=completed,
            failed_runs=failed,
            pending_runs=pending,
            progress_percent=progress,
            started_at=experiment.started_at,
            completed_at=experiment.completed_at,
        )

    async def run_experiment(self, db: AsyncSession, experiment_id: str) -> None:
        """
        Execute the experiment. Runs synchronously (for environments without Celery/Redis).
        In production, this should be dispatched to Celery.
        """
        experiment = await self.get_experiment(db, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        if experiment.status not in (ExperimentStatus.pending, ExperimentStatus.failed):
            raise ValueError(f"Experiment is not in a runnable state: {experiment.status}")

        config = experiment.configuration or {}
        lang_filter = config.get("language_filter")
        cat_filter = config.get("category_filter")
        prompt_template = config.get("prompt_template", "Question: {question_text}\n\nPlease provide a concise, accurate answer.")
        system_prompt = config.get("system_prompt")
        temperature = config.get("temperature", 0.0)
        max_tokens = config.get("max_tokens", 512)
        timeout = config.get("request_timeout", 60)
        max_retries = config.get("max_retries", 3)
        concurrency = config.get("concurrency_limit", 5)

        # Mark as running
        experiment.status = ExperimentStatus.running
        experiment.started_at = datetime.now(timezone.utc)
        await db.flush()
        await self._add_event(db, experiment_id, "started", "Experiment execution started.")

        try:
            # Load questions
            q = select(Question).where(Question.dataset_id == experiment.dataset_version_id)
            if lang_filter:
                q = q.where(Question.language.in_(lang_filter))
            if cat_filter:
                q = q.where(Question.category.in_(cat_filter))
            questions_result = await db.execute(q)
            questions: List[Question] = questions_result.scalars().all()

            # Load model selections
            em_result = await db.execute(
                select(ExperimentModel).where(ExperimentModel.experiment_id == experiment_id)
            )
            exp_models: List[ExperimentModel] = em_result.scalars().all()

            total = len(questions) * len(exp_models)
            await self._add_event(db, experiment_id, "planned", f"Will run {total} model requests ({len(questions)} questions × {len(exp_models)} models)")

            success_count = 0
            fail_count = 0

            # Run with concurrency control
            sem = asyncio.Semaphore(concurrency)

            async def run_one(question: Question, exp_model: ExperimentModel):
                nonlocal success_count, fail_count

                async with sem:
                    # Check if already successfully completed (avoid duplicates)
                    existing = await db.execute(
                        select(ModelRun).where(
                            and_(
                                ModelRun.experiment_id == experiment_id,
                                ModelRun.question_id == question.id,
                                ModelRun.provider == exp_model.provider,
                                ModelRun.model_name == exp_model.model_name,
                                ModelRun.response_status == RunStatus.completed,
                            )
                        )
                    )
                    if existing.scalar_one_or_none():
                        return  # Already done, skip

                    # Create or get existing run
                    run_result = await db.execute(
                        select(ModelRun).where(
                            and_(
                                ModelRun.experiment_id == experiment_id,
                                ModelRun.question_id == question.id,
                                ModelRun.provider == exp_model.provider,
                                ModelRun.model_name == exp_model.model_name,
                            )
                        )
                    )
                    model_run = run_result.scalar_one_or_none()

                    if not model_run:
                        model_run = ModelRun(
                            experiment_id=experiment_id,
                            question_id=question.id,
                            provider=exp_model.provider,
                            model_name=exp_model.model_name,
                            response_status=RunStatus.pending,
                        )
                        db.add(model_run)
                        await db.flush()

                    model_run.response_status = RunStatus.running

                    # Build prompt
                    ctx = question.context or ""
                    prompt = prompt_template.format(
                        question_text=question.question_text,
                        context=ctx,
                        language=question.language.value,
                        category=question.category.value,
                    )
                    model_run.prompt = prompt

                    provider = provider_registry.get_provider(exp_model.provider)
                    if not provider:
                        model_run.response_status = RunStatus.failed
                        model_run.error_message = f"Provider '{exp_model.provider}' not found"
                        fail_count += 1
                        await db.flush()
                        return

                    request = LLMRequest(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=timeout,
                    )

                    # Retry logic
                    llm_response = None
                    last_error = None
                    for attempt in range(max_retries + 1):
                        try:
                            llm_response = await provider.generate(request, exp_model.model_name)
                            if llm_response.error:
                                last_error = llm_response.error
                                model_run.retry_count = attempt
                                if attempt < max_retries:
                                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            break
                        except Exception as e:
                            last_error = str(e)
                            model_run.retry_count = attempt
                            if attempt < max_retries:
                                await asyncio.sleep(2 ** attempt)

                    if llm_response and not llm_response.error:
                        model_run.response_text = llm_response.text
                        model_run.response_status = RunStatus.completed
                        model_run.model_version = llm_response.model_version
                        model_run.latency_ms = llm_response.latency_ms
                        model_run.input_tokens = llm_response.input_tokens
                        model_run.output_tokens = llm_response.output_tokens
                        model_run.is_demo = llm_response.is_demo
                        success_count += 1
                    else:
                        model_run.response_status = RunStatus.failed
                        model_run.error_message = last_error or "Unknown error"
                        fail_count += 1

                    await db.flush()

            # Execute all tasks
            tasks = [run_one(q, em) for q in questions for em in exp_models]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Determine final status
            if fail_count == 0:
                experiment.status = ExperimentStatus.completed
            elif success_count == 0:
                experiment.status = ExperimentStatus.failed
            else:
                experiment.status = ExperimentStatus.partially_completed

            experiment.completed_at = datetime.now(timezone.utc)
            await db.flush()
            await self._add_event(
                db, experiment_id, "completed",
                f"Experiment finished. Successful: {success_count}, Failed: {fail_count}"
            )

        except Exception as e:
            logger.exception(f"Experiment {experiment_id} crashed")
            experiment.status = ExperimentStatus.failed
            experiment.completed_at = datetime.now(timezone.utc)
            await db.flush()
            await self._add_event(db, experiment_id, "error", str(e))
            raise

    async def cancel_experiment(self, db: AsyncSession, experiment_id: str) -> Experiment:
        """Cancel a running experiment."""
        experiment = await self.get_experiment(db, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")
        if experiment.status not in (ExperimentStatus.pending, ExperimentStatus.queued, ExperimentStatus.running):
            raise ValueError("Cannot cancel an experiment that is not active")
        experiment.status = ExperimentStatus.cancelled
        await db.flush()
        await self._add_event(db, experiment_id, "cancelled", "Experiment cancelled by user.")
        return experiment

    async def retry_failed(self, db: AsyncSession, experiment_id: str) -> int:
        """Reset failed model runs to pending for retry. Returns count of runs reset."""
        result = await db.execute(
            select(ModelRun).where(
                and_(
                    ModelRun.experiment_id == experiment_id,
                    ModelRun.response_status == RunStatus.failed,
                )
            )
        )
        failed_runs = result.scalars().all()
        for run in failed_runs:
            run.response_status = RunStatus.pending
            run.error_message = None

        await db.flush()
        await self._add_event(db, experiment_id, "retry_queued", f"Reset {len(failed_runs)} failed runs for retry.")
        return len(failed_runs)

    async def _add_event(
        self,
        db: AsyncSession,
        experiment_id: str,
        event_type: str,
        message: str,
        data: Optional[Dict] = None,
    ):
        event = ExperimentEvent(
            experiment_id=experiment_id,
            event_type=event_type,
            message=message,
            data=data,
        )
        db.add(event)
        await db.flush()


experiment_service = ExperimentService()
