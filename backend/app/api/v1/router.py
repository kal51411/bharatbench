"""API v1 router aggregator."""
from fastapi import APIRouter
from app.api.v1.routes import auth, datasets, questions, experiments, evaluations, analytics, providers, exports

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(datasets.router)
api_router.include_router(questions.router)
api_router.include_router(experiments.router)
api_router.include_router(evaluations.router)
api_router.include_router(analytics.router)
api_router.include_router(providers.router)
api_router.include_router(exports.router)
