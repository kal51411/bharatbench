"""Initial BharatBench schema."""
from alembic import op
from sqlalchemy import inspect
from app.db.base import Base
from app.models import models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in inspector.get_table_names():
            table.drop(bind=bind)
