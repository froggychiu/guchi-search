"""Additive schema reconciliation at startup.

This project has no migrations. Schema comes from `Base.metadata.create_all`
in the FastAPI lifespan, which creates missing *tables* — and silently ignores
missing *columns* on tables that already exist.

That gap bit us the first time a column was added to an existing table:
vocab_rules had been created by an earlier deploy, so adding `source` and
`evidence_count` to the model left production selecting columns the table did
not have. Every read of that table 500'd.

So after create_all, compare each model against the live table and ADD COLUMN
for anything missing. Deliberately additive only — never drops a column, never
changes a type, never touches data. Those are the changes that need a real
migration and a human; adding a column is the one create_all was already
pretending to handle.

If this ever needs to do more than add columns, that is the signal to adopt
alembic (already in requirements.txt, currently unused) rather than to grow
this file.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

logger = logging.getLogger(__name__)


def _column_ddl(dialect, column) -> str:
    """Render `name TYPE [DEFAULT x] [NOT NULL]` for this dialect.

    A new column on a populated table cannot be NOT NULL without a default —
    existing rows have nothing to put there — so a default is required before
    the constraint is emitted.
    """
    spec = CreateColumn(column).compile(dialect=dialect).string

    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        literal = f"'{value}'" if isinstance(value, str) else str(value)
        spec += f" DEFAULT {literal}"
    elif not column.nullable:
        # No default to backfill with, so the constraint would reject existing
        # rows. Add it nullable and leave the mismatch visible in the log
        # rather than failing startup.
        spec = spec.replace(" NOT NULL", "")
        logger.warning(
            "Column %s.%s is NOT NULL in the model but has no default; "
            "adding it as nullable.", column.table.name, column.name,
        )

    return spec


def sync_columns(connection, metadata) -> list[str]:
    """Add model columns that are missing from existing tables.

    Runs inside `run_sync`, so `connection` is a sync Connection. Returns the
    "table.column" names it added.
    """
    inspector = inspect(connection)
    present_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in metadata.sorted_tables:
        if table.name not in present_tables:
            continue  # create_all just made it, or will
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = _column_ddl(connection.dialect, column)
            connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
            added.append(f"{table.name}.{column.name}")

    if added:
        logger.info("Added missing columns: %s", ", ".join(added))
    return added
