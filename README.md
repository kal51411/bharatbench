# BharatBench

BharatBench is an evaluation workspace for Indian-language and Indian-context
LLMs. The local release uses SQLite, the built-in mock provider, and FastAPI
background execution; paid provider keys are optional.

## Local development

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -e 'backend[test]'
alembic upgrade head
.venv/bin/uvicorn app.main:app --app-dir backend --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

The development admin credentials come from `.env`. Set a strong
`SECRET_KEY`, `ADMIN_PASSWORD`, and `AUTO_CREATE_TABLES=false` outside local
development, then run `alembic upgrade head`.

## Verification

```bash
cd backend
../.venv/bin/pytest
cd ../frontend
npm run build
```

The frontend reads all metrics and records from the API and marks the local
mock-provider workspace as `DEMO`.

To seed the included bilingual demo corpus on startup, set
`DEMO_DATASET_ENABLED=true`. The Render blueprint enables this for the demo
service.
