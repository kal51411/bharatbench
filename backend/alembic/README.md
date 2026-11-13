# Database migrations

From the repository root:

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe change"
alembic downgrade -1
```

Development may use `AUTO_CREATE_TABLES=true`; production should set it to
`false` and run migrations explicitly.
