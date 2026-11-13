"""SQLAlchemy ORM Models for BharatBench."""
import enum
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Any

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, Enum as SAEnum,
    ForeignKey, JSON, Index, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


def utcnow():
    return datetime.now(timezone.utc)


def new_uuid():
    return str(uuid.uuid4())


# ─────────────────────────── ENUMS ───────────────────────────

class UserRole(str, enum.Enum):
    admin = "admin"
    researcher = "researcher"
    reviewer = "reviewer"


class DatasetStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class QuestionType(str, enum.Enum):
    factual = "factual"
    multiple_choice = "multiple_choice"
    open_ended = "open_ended"
    bias_sensitive = "bias_sensitive"


class Language(str, enum.Enum):
    english = "english"
    hindi = "hindi"
    marathi = "marathi"
    tamil = "tamil"
    bengali = "bengali"
    telugu = "telugu"
    hinglish = "hinglish"
    other = "other"


class Category(str, enum.Enum):
    history = "history"
    geography = "geography"
    civics = "civics"
    science = "science"
    general_knowledge = "general_knowledge"
    culture = "culture"
    bias_sensitive = "bias_sensitive"
    other = "other"


class ExperimentStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    running = "running"
    completed = "completed"
    partially_completed = "partially_completed"
    failed = "failed"
    cancelled = "cancelled"


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class CorrectnessLabel(str, enum.Enum):
    correct = "correct"
    partially_correct = "partially_correct"
    incorrect = "incorrect"
    unanswerable = "unanswerable"
    needs_adjudication = "needs_adjudication"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    reviewed = "reviewed"
    adjudicated = "adjudicated"


class BiasCategory(str, enum.Enum):
    caste = "caste"
    religion = "religion"
    gender = "gender"
    region = "region"
    language = "language"
    other = "other"


class BiasSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class EvaluationMethod(str, enum.Enum):
    exact_match = "exact_match"
    accepted_answer_match = "accepted_answer_match"
    keyword_match = "keyword_match"
    human = "human"
    mock = "mock"


# ─────────────────────────── MODELS ───────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.researcher, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    datasets: Mapped[List["Dataset"]] = relationship("Dataset", back_populates="creator", foreign_keys="Dataset.created_by_id")
    experiments: Mapped[List["Experiment"]] = relationship("Experiment", back_populates="creator", foreign_keys="Experiment.created_by_id")
    evaluations: Mapped[List["Evaluation"]] = relationship("Evaluation", back_populates="evaluator")


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    status: Mapped[DatasetStatus] = mapped_column(SAEnum(DatasetStatus), default=DatasetStatus.draft, nullable=False)
    source_format: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # csv, json
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    creator: Mapped["User"] = relationship("User", back_populates="datasets", foreign_keys=[created_by_id])
    questions: Mapped[List["Question"]] = relationship("Question", back_populates="dataset")
    experiments: Mapped[List["Experiment"]] = relationship("Experiment", back_populates="dataset_version")

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_dataset_name_version"),
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id"), nullable=False)
    external_question_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comparison_group_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[Language] = mapped_column(SAEnum(Language), nullable=False)
    category: Mapped[Category] = mapped_column(SAEnum(Category), nullable=False)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    question_type: Mapped[QuestionType] = mapped_column(SAEnum(QuestionType), default=QuestionType.factual)
    reference_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acceptable_answers: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)  # list of strings
    evaluation_rubric: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="questions")
    model_runs: Mapped[List["ModelRun"]] = relationship("ModelRun", back_populates="question")

    __table_args__ = (
        Index("ix_questions_dataset_language", "dataset_id", "language"),
        Index("ix_questions_dataset_category", "dataset_id", "category"),
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    experiment_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dataset_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id"), nullable=False)
    status: Mapped[ExperimentStatus] = mapped_column(SAEnum(ExperimentStatus), default=ExperimentStatus.pending, nullable=False)
    configuration: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_by_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Celery task ID
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    creator: Mapped["User"] = relationship("User", back_populates="experiments", foreign_keys=[created_by_id])
    dataset_version: Mapped["Dataset"] = relationship("Dataset", back_populates="experiments")
    experiment_models: Mapped[List["ExperimentModel"]] = relationship("ExperimentModel", back_populates="experiment", cascade="all, delete-orphan")
    model_runs: Mapped[List["ModelRun"]] = relationship("ModelRun", back_populates="experiment")
    events: Mapped[List["ExperimentEvent"]] = relationship("ExperimentEvent", back_populates="experiment", cascade="all, delete-orphan")


class ExperimentModel(Base):
    __tablename__ = "experiment_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    experiment_id: Mapped[str] = mapped_column(String(36), ForeignKey("experiments.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_config: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)

    # Relationships
    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="experiment_models")

    __table_args__ = (
        UniqueConstraint("experiment_id", "provider", "model_name", name="uq_experiment_provider_model"),
    )


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    experiment_id: Mapped[str] = mapped_column(String(36), ForeignKey("experiments.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("questions.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus), default=RunStatus.pending, nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="model_runs")
    question: Mapped["Question"] = relationship("Question", back_populates="model_runs")
    evaluations: Mapped[List["Evaluation"]] = relationship("Evaluation", back_populates="model_run")

    __table_args__ = (
        Index("ix_model_runs_experiment_question", "experiment_id", "question_id"),
        Index("ix_model_runs_provider_model", "provider", "model_name"),
        # Prevent duplicate successful runs
        UniqueConstraint("experiment_id", "question_id", "provider", "model_name", name="uq_model_run"),
    )


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    model_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("model_runs.id"), nullable=False)
    evaluator_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)  # null = auto
    evaluation_method: Mapped[EvaluationMethod] = mapped_column(SAEnum(EvaluationMethod), default=EvaluationMethod.human)
    evaluation_method_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    correctness_label: Mapped[Optional[CorrectnessLabel]] = mapped_column(SAEnum(CorrectnessLabel), nullable=True)
    correctness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0.0, 0.5, 1.0
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_status: Mapped[ReviewStatus] = mapped_column(SAEnum(ReviewStatus), default=ReviewStatus.pending)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    model_run: Mapped["ModelRun"] = relationship("ModelRun", back_populates="evaluations")
    evaluator: Mapped[Optional["User"]] = relationship("User", back_populates="evaluations")
    bias_annotations: Mapped[List["BiasAnnotation"]] = relationship("BiasAnnotation", back_populates="evaluation", cascade="all, delete-orphan")
    audit_logs: Mapped[List["EvaluationAuditLog"]] = relationship("EvaluationAuditLog", back_populates="evaluation")

    __table_args__ = (
        Index("ix_evaluations_model_run", "model_run_id"),
        # Allow multiple independent reviews per model run (different evaluators)
        # Unique per model_run + evaluator combo for human reviews
        UniqueConstraint("model_run_id", "evaluator_id", name="uq_evaluation_run_evaluator"),
    )


class BiasAnnotation(Base):
    __tablename__ = "bias_annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    evaluation_id: Mapped[str] = mapped_column(String(36), ForeignKey("evaluations.id"), nullable=False)
    is_bias_flagged: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # null = pending
    bias_category: Mapped[Optional[BiasCategory]] = mapped_column(SAEnum(BiasCategory), nullable=True)
    severity: Mapped[Optional[BiasSeverity]] = mapped_column(SAEnum(BiasSeverity), nullable=True)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    evaluation: Mapped["Evaluation"] = relationship("Evaluation", back_populates="bias_annotations")


class EvaluationAuditLog(Base):
    __tablename__ = "evaluation_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    evaluation_id: Mapped[str] = mapped_column(String(36), ForeignKey("evaluations.id"), nullable=False)
    changed_by_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    field_changed: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    evaluation: Mapped["Evaluation"] = relationship("Evaluation", back_populates="audit_logs")


class ExperimentEvent(Base):
    __tablename__ = "experiment_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    experiment_id: Mapped[str] = mapped_column(String(36), ForeignKey("experiments.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="events")
