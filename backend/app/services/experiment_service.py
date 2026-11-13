"""Experiment creation, execution, progress tracking, and cancellation."""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.models.models import (
    Experiment, ExperimentModel, ExperimentEvent, ModelRun, Question,
    ExperimentStatus, RunStatus,
)
from app.schemas.schemas import ExperimentCreate, ExperimentProgress
from app.services.providers.base import provider_registry, LLMRequest

logger = logging.getLogger(__name__)


@dataclass
class WorkerResult:
    question_id: str
    provider: str
    model_name: str
    prompt: str
    response: Any = None
    error: Optional[str] = None
    cancelled: bool = False


class ExperimentService:
    def __init__(self):
        self._active: set[str] = set()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def create_experiment(self, db: AsyncSession, exp_in: ExperimentCreate, created_by_id: str) -> Experiment:
        experiment = Experiment(
            experiment_name=exp_in.experiment_name,
            description=exp_in.description,
            dataset_version_id=exp_in.dataset_version_id,
            status=ExperimentStatus.pending,
            configuration=exp_in.configuration.model_dump(),
            created_by_id=created_by_id,
        )
        db.add(experiment)
        await db.flush()
        for model_sel in exp_in.models:
            db.add(ExperimentModel(
                experiment_id=experiment.id,
                provider=model_sel.provider,
                model_name=model_sel.model_name,
                model_config=model_sel.model_config_extra,
            ))
        await self._add_event(db, experiment.id, "created", f"Experiment '{exp_in.experiment_name}' created.")
        await db.refresh(experiment)
        return experiment

    async def get_experiment(self, db: AsyncSession, experiment_id: str) -> Optional[Experiment]:
        result = await db.execute(select(Experiment).where(Experiment.id == experiment_id))
        return result.scalar_one_or_none()

    async def list_experiments(self, db: AsyncSession, skip: int = 0, limit: int = 20) -> Tuple[List[Experiment], int]:
        total = (await db.execute(select(func.count(Experiment.id)))).scalar_one()
        result = await db.execute(select(Experiment).order_by(Experiment.created_at.desc()).offset(skip).limit(limit))
        return result.scalars().all(), total

    async def get_progress(self, db: AsyncSession, experiment_id: str) -> ExperimentProgress:
        experiment = await self.get_experiment(db, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")
        result = await db.execute(
            select(ModelRun.response_status, func.count(ModelRun.id))
            .where(ModelRun.experiment_id == experiment_id)
            .group_by(ModelRun.response_status)
        )
        counts = {status: count for status, count in result.all()}
        completed = counts.get(RunStatus.completed, 0)
        failed = counts.get(RunStatus.failed, 0)
        total_runs = sum(counts.values())
        pending = counts.get(RunStatus.pending, 0) + counts.get(RunStatus.running, 0)
        config = experiment.configuration or {}
        query = select(func.count(Question.id)).where(Question.dataset_id == experiment.dataset_version_id)
        if config.get("language_filter"):
            query = query.where(Question.language.in_(config["language_filter"]))
        if config.get("category_filter"):
            query = query.where(Question.category.in_(config["category_filter"]))
        total_questions = (await db.execute(query)).scalar_one()
        progress = round((completed + failed) / total_runs * 100, 1) if total_runs else 0.0
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

    async def _worker(
        self,
        question: Question,
        exp_model: ExperimentModel,
        config: Dict[str, Any],
        cancel_event: asyncio.Event,
        semaphore: asyncio.Semaphore,
    ) -> WorkerResult:
        async with semaphore:
            ctx = question.context or ""
            prompt = config.get("prompt_template", "Question: {question_text}\n\nPlease provide a concise, accurate answer.").format(
                question_text=question.question_text,
                context=ctx,
                language=question.language.value,
                category=question.category.value,
            )
            if cancel_event.is_set():
                return WorkerResult(question.id, exp_model.provider, exp_model.model_name, prompt, cancelled=True)
            provider = provider_registry.get_provider(exp_model.provider)
            if not provider:
                return WorkerResult(question.id, exp_model.provider, exp_model.model_name, prompt,
                                    error=f"Provider '{exp_model.provider}' not found")
            request = LLMRequest(
                prompt=prompt,
                system_prompt=config.get("system_prompt"),
                temperature=config.get("temperature", 0.0),
                max_tokens=config.get("max_tokens", 512),
                timeout=config.get("request_timeout", 60),
            )
            last_error = None
            retries = config.get("max_retries", 3)
            for attempt in range(retries + 1):
                if cancel_event.is_set():
                    return WorkerResult(question.id, exp_model.provider, exp_model.model_name, prompt, cancelled=True)
                try:
                    response = await provider.generate(request, exp_model.model_name)
                    if not response.error:
                        return WorkerResult(question.id, exp_model.provider, exp_model.model_name, prompt, response=response)
                    last_error = response.error
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = str(exc)
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)
            return WorkerResult(question.id, exp_model.provider, exp_model.model_name, prompt, error=last_error or "Unknown provider error")

    async def run_experiment(self, db: AsyncSession, experiment_id: str) -> None:
        async with self._lock:
            if experiment_id in self._active:
                raise ValueError("Experiment is already running")
            self._active.add(experiment_id)
            cancel_event = self._cancel_events.setdefault(experiment_id, asyncio.Event())
        try:
            experiment = await self.get_experiment(db, experiment_id)
            if not experiment:
                raise ValueError(f"Experiment {experiment_id} not found")
            if experiment.status not in (ExperimentStatus.pending, ExperimentStatus.queued, ExperimentStatus.failed):
                raise ValueError(f"Experiment is not in a runnable state: {experiment.status}")
            experiment.status = ExperimentStatus.running
            experiment.started_at = datetime.now(timezone.utc)
            await self._add_event(db, experiment_id, "started", "Experiment execution started.")

            config = experiment.configuration or {}
            query = select(Question).where(Question.dataset_id == experiment.dataset_version_id)
            if config.get("language_filter"):
                query = query.where(Question.language.in_(config["language_filter"]))
            if config.get("category_filter"):
                query = query.where(Question.category.in_(config["category_filter"]))
            questions = (await db.execute(query)).scalars().all()
            exp_models = (await db.execute(
                select(ExperimentModel).where(ExperimentModel.experiment_id == experiment_id)
            )).scalars().all()
            existing = (await db.execute(select(ModelRun).where(ModelRun.experiment_id == experiment_id))).scalars().all()
            completed_keys = {
                (r.question_id, r.provider, r.model_name)
                for r in existing if r.response_status == RunStatus.completed
            }
            work = [
                (q, em) for q in questions for em in exp_models
                if (q.id, em.provider, em.model_name) not in completed_keys
            ]
            await self._add_event(db, experiment_id, "planned", f"Will run {len(work)} new model requests.")
            semaphore = asyncio.Semaphore(config.get("concurrency_limit", 5))
            tasks = [asyncio.create_task(self._worker(q, em, config, cancel_event, semaphore)) for q, em in work]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)

            successes = failures = cancelled = 0
            existing_by_key = {(r.question_id, r.provider, r.model_name): r for r in existing}
            for item in gathered:
                key = None
                if isinstance(item, WorkerResult):
                    key = (item.question_id, item.provider, item.model_name)
                    model_run = existing_by_key.get(key)
                    if model_run is None:
                        model_run = ModelRun(
                            experiment_id=experiment_id,
                            question_id=item.question_id,
                            provider=item.provider,
                            model_name=item.model_name,
                        )
                        db.add(model_run)
                        existing_by_key[key] = model_run
                    model_run.prompt = item.prompt
                    if item.cancelled:
                        cancelled += 1
                        model_run.response_status = RunStatus.pending
                    elif item.error:
                        failures += 1
                        model_run.response_status = RunStatus.failed
                        model_run.error_message = item.error
                    else:
                        successes += 1
                        response = item.response
                        model_run.response_text = response.text
                        model_run.response_status = RunStatus.completed
                        model_run.model_version = response.model_version
                        model_run.latency_ms = response.latency_ms
                        model_run.input_tokens = response.input_tokens
                        model_run.output_tokens = response.output_tokens
                        model_run.is_demo = response.is_demo
                else:
                    failures += 1
                    logger.error("Unexpected experiment worker failure: %s", item)
            if cancel_event.is_set():
                experiment.status = ExperimentStatus.cancelled
                await self._add_event(db, experiment_id, "cancelled", "Experiment cancelled; in-flight work was allowed to finish.")
            elif failures and not successes:
                experiment.status = ExperimentStatus.failed
            elif failures:
                experiment.status = ExperimentStatus.partially_completed
            else:
                experiment.status = ExperimentStatus.completed
            experiment.completed_at = datetime.now(timezone.utc)
            await self._add_event(db, experiment_id, "completed", f"Successful: {successes}, failed: {failures}, cancelled: {cancelled}")
            await db.flush()
        except Exception as exc:
            logger.exception("Experiment %s crashed", experiment_id)
            experiment = await self.get_experiment(db, experiment_id)
            if experiment:
                experiment.status = ExperimentStatus.failed
                experiment.completed_at = datetime.now(timezone.utc)
                await self._add_event(db, experiment_id, "error", str(exc))
                await db.flush()
            raise
        finally:
            async with self._lock:
                self._active.discard(experiment_id)
                self._cancel_events.pop(experiment_id, None)

    async def cancel_experiment(self, db: AsyncSession, experiment_id: str) -> Experiment:
        experiment = await self.get_experiment(db, experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")
        if experiment.status not in (ExperimentStatus.pending, ExperimentStatus.queued, ExperimentStatus.running):
            raise ValueError("Cannot cancel an experiment that is not active")
        event = self._cancel_events.get(experiment_id)
        if event:
            event.set()
        experiment.status = ExperimentStatus.cancelled
        await self._add_event(db, experiment_id, "cancelled", "Experiment cancellation requested by user.")
        await db.flush()
        return experiment

    async def retry_failed(self, db: AsyncSession, experiment_id: str) -> int:
        result = await db.execute(select(ModelRun).where(and_(
            ModelRun.experiment_id == experiment_id,
            ModelRun.response_status == RunStatus.failed,
        )))
        failed_runs = result.scalars().all()
        for run in failed_runs:
            run.response_status = RunStatus.pending
            run.error_message = None
        experiment = await self.get_experiment(db, experiment_id)
        if experiment and experiment.status in (ExperimentStatus.failed, ExperimentStatus.partially_completed):
            experiment.status = ExperimentStatus.queued
        await self._add_event(db, experiment_id, "retry_queued", f"Reset {len(failed_runs)} failed runs for retry.")
        return len(failed_runs)

    async def _add_event(self, db: AsyncSession, experiment_id: str, event_type: str, message: str, data: Optional[Dict] = None):
        db.add(ExperimentEvent(experiment_id=experiment_id, event_type=event_type, message=message, data=data))
        await db.flush()


experiment_service = ExperimentService()
