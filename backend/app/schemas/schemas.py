"""Pydantic v2 schemas for BharatBench API."""
from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from app.models.models import (
    UserRole, DatasetStatus, Language, Category, QuestionType,
    ExperimentStatus, RunStatus, CorrectnessLabel, ReviewStatus,
    BiasCategory, BiasSeverity, EvaluationMethod
)


# ─────────────────────────── COMMON ───────────────────────────

class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


class MessageResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ─────────────────────────── AUTH ───────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: UserRole


class UserPublic(BaseModel):
    id: str
    username: str
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.researcher


# ─────────────────────────── DATASETS ───────────────────────────

class DatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    version: str = Field(default="1.0", max_length=50)


class DatasetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[DatasetStatus] = None


class DatasetPublic(BaseModel):
    id: str
    name: str
    description: Optional[str]
    version: str
    status: DatasetStatus
    source_format: Optional[str]
    row_count: int
    created_by_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ValidationError(BaseModel):
    row: int
    field: str
    message: str


class DatasetValidationResult(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    errors: List[ValidationError]
    warnings: List[str]
    preview: List[Dict[str, Any]]  # First 5 rows
    duplicate_ids: List[str]


# ─────────────────────────── QUESTIONS ───────────────────────────

class QuestionCreate(BaseModel):
    dataset_id: str
    external_question_id: Optional[str] = None
    comparison_group_id: Optional[str] = None
    question_text: str = Field(..., min_length=1)
    language: Language
    category: Category
    context: Optional[str] = None
    question_type: QuestionType = QuestionType.factual
    reference_answer: Optional[str] = None
    acceptable_answers: Optional[List[str]] = None
    evaluation_rubric: Optional[str] = None


class QuestionPublic(BaseModel):
    id: str
    dataset_id: str
    external_question_id: Optional[str]
    comparison_group_id: Optional[str]
    question_text: str
    language: Language
    category: Category
    context: Optional[str]
    question_type: QuestionType
    reference_answer: Optional[str]
    acceptable_answers: Optional[List[str]]
    evaluation_rubric: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────── EXPERIMENTS ───────────────────────────

class ModelSelection(BaseModel):
    provider: str
    model_name: str
    model_config_extra: Optional[Dict[str, Any]] = Field(default=None, alias="model_config")

    model_config = {"populate_by_name": True}


class ExperimentConfig(BaseModel):
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    system_prompt: Optional[str] = None
    prompt_template: str = Field(
        default="Question: {question_text}\n\nPlease provide a concise, accurate answer.",
    )
    request_timeout: int = Field(default=60, ge=5, le=300)
    max_retries: int = Field(default=3, ge=0, le=10)
    concurrency_limit: int = Field(default=5, ge=1, le=20)
    language_filter: Optional[List[str]] = None  # None = all languages
    category_filter: Optional[List[str]] = None  # None = all categories


class ExperimentCreate(BaseModel):
    experiment_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    dataset_version_id: str
    models: List[ModelSelection] = Field(..., min_length=1)
    configuration: ExperimentConfig = Field(default_factory=ExperimentConfig)


class ExperimentPublic(BaseModel):
    id: str
    experiment_name: str
    description: Optional[str]
    dataset_version_id: str
    status: ExperimentStatus
    configuration: Optional[Dict]
    created_by_id: str
    task_id: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExperimentProgress(BaseModel):
    experiment_id: str
    status: ExperimentStatus
    total_questions: int
    total_runs: int
    completed_runs: int
    failed_runs: int
    pending_runs: int
    progress_percent: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class ExperimentModelPublic(BaseModel):
    id: str
    experiment_id: str
    provider: str
    model_name: str
    model_config: Optional[Dict]

    model_config = {"from_attributes": True}


# ─────────────────────────── MODEL RUNS ───────────────────────────

class ModelRunPublic(BaseModel):
    id: str
    experiment_id: str
    question_id: str
    provider: str
    model_name: str
    model_version: Optional[str]
    prompt: Optional[str]
    response_text: Optional[str]
    response_status: RunStatus
    latency_ms: Optional[int]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    error_message: Optional[str]
    retry_count: int
    is_demo: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────── EVALUATIONS ───────────────────────────

class EvaluationCreate(BaseModel):
    model_run_id: str
    correctness_label: CorrectnessLabel
    correctness_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    notes: Optional[str] = None
    # Bias annotation (optional, included with evaluation)
    is_bias_flagged: Optional[bool] = None
    bias_category: Optional[BiasCategory] = None
    bias_severity: Optional[BiasSeverity] = None
    bias_evidence: Optional[str] = None
    bias_reviewer_notes: Optional[str] = None

    @model_validator(mode="after")
    def set_correctness_score(self):
        """Auto-set score from label if not provided."""
        if self.correctness_score is None:
            score_map = {
                CorrectnessLabel.correct: 1.0,
                CorrectnessLabel.partially_correct: 0.5,
                CorrectnessLabel.incorrect: 0.0,
                CorrectnessLabel.unanswerable: None,
                CorrectnessLabel.needs_adjudication: None,
            }
            self.correctness_score = score_map.get(self.correctness_label)
        return self


class EvaluationUpdate(BaseModel):
    correctness_label: Optional[CorrectnessLabel] = None
    correctness_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_status: Optional[ReviewStatus] = None
    notes: Optional[str] = None


class BiasAnnotationPublic(BaseModel):
    id: str
    evaluation_id: str
    is_bias_flagged: Optional[bool]
    bias_category: Optional[BiasCategory]
    severity: Optional[BiasSeverity]
    evidence: Optional[str]
    reviewer_notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class EvaluationPublic(BaseModel):
    id: str
    model_run_id: str
    evaluator_id: Optional[str]
    evaluation_method: EvaluationMethod
    correctness_label: Optional[CorrectnessLabel]
    correctness_score: Optional[float]
    confidence: Optional[float]
    review_status: ReviewStatus
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    bias_annotations: List[BiasAnnotationPublic] = []

    model_config = {"from_attributes": True}


# ─────────────────────────── ANALYTICS ───────────────────────────

class ModelAccuracy(BaseModel):
    provider: str
    model_name: str
    total_evaluated: int
    correct: int
    partially_correct: int
    incorrect: int
    full_credit_accuracy: float
    partial_credit_score: float
    avg_latency_ms: Optional[float]
    bias_rate: Optional[float]


class LanguageAccuracy(BaseModel):
    language: str
    total_evaluated: int
    full_credit_accuracy: float
    partial_credit_score: float
    vs_english_diff: Optional[float]


class CategoryAccuracy(BaseModel):
    category: str
    total_evaluated: int
    full_credit_accuracy: float
    partial_credit_score: float


class BiasAnalysis(BaseModel):
    total_reviewed: int
    bias_flagged: int
    not_flagged: int
    pending: int
    bias_rate: float
    by_category: Dict[str, int]
    by_model: List[Dict[str, Any]]
    by_language: List[Dict[str, Any]]


class ConsistencyResult(BaseModel):
    comparison_group_id: str
    languages_present: List[str]
    model_name: str
    consistent: Optional[bool]
    scores: Dict[str, Optional[float]]


class OverviewStats(BaseModel):
    total_datasets: int
    total_questions: int
    total_experiments: int
    total_model_runs: int
    completed_runs: int
    pending_evaluations: int
    overall_accuracy: Optional[float]
    reviewed_bias_rate: Optional[float]
    configured_providers: List[str]


# ─────────────────────────── PROVIDERS ───────────────────────────

class ProviderStatus(BaseModel):
    provider: str
    is_configured: bool
    is_available: bool
    models: List[str]
    error: Optional[str]


class ProviderInfo(BaseModel):
    provider: str
    display_name: str
    models: List[str]
    is_configured: bool


# ─────────────────────────── EXPORTS ───────────────────────────

class ExportRequest(BaseModel):
    experiment_id: str
    include_prompts: bool = True
    include_responses: bool = True
    include_evaluations: bool = True
    include_bias: bool = True
