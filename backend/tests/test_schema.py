"""Tests for additive schema reconciliation.

Run with:  python backend/tests/test_schema.py

Reproduces the failure this was written for: vocab_rules existed from an
earlier deploy, the model gained `source` and `evidence_count`, and
create_all silently left the table alone — so every read of it 500'd with
UndefinedColumnError in production.
"""

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "schema_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, os.path.dirname(HERE))

from sqlalchemy import Integer, String, inspect, text  # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column  # noqa: E402

from app.core.database import Base, engine  # noqa: E402
from app.core.schema import sync_columns  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


class Widget(Base):
    """Stands in for a model that gains fields after its table exists."""
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    # These three arrive "later" — the table is created without them below.
    source: Mapped[str] = mapped_column(String(20), default="correction")
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(String(100), nullable=True)


async def main():
    # Build the table as an older deploy would have: id and name only.
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(50))"
        ))
        await conn.execute(text("INSERT INTO widgets (id, name) VALUES (1, 'old row')"))

    async with engine.begin() as conn:
        cols = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("widgets")}
        )
    check("starts without the new columns", sorted(cols), ["id", "name"])

    # create_all alone is what production was relying on.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        cols = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("widgets")}
        )
    check("create_all does NOT add them (the original bug)",
          sorted(cols), ["id", "name"])

    async with engine.begin() as conn:
        added = await conn.run_sync(lambda c: sync_columns(c, Base.metadata))
    check("sync_columns adds exactly the missing ones",
          sorted(added),
          ["widgets.evidence_count", "widgets.note", "widgets.source"])

    async with engine.begin() as conn:
        cols = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("widgets")}
        )
    check("table now matches the model",
          sorted(cols), ["evidence_count", "id", "name", "note", "source"])

    # The pre-existing row must survive and pick up the model defaults.
    async with engine.begin() as conn:
        row = (await conn.execute(text(
            "SELECT name, source, evidence_count, note FROM widgets WHERE id = 1"
        ))).one()
    check("existing row preserved", row[0], "old row")
    check("backfilled with the model default", row[1], "correction")
    check("numeric default backfilled", row[2], 1)
    check("nullable column left null", row[3], None)

    # Running again must be a no-op, since it runs on every startup.
    async with engine.begin() as conn:
        again = await conn.run_sync(lambda c: sync_columns(c, Base.metadata))
    check("idempotent across restarts", again, [])

    await engine.dispose()
    print("\n" + "=" * 60)
    if failures:
        print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
        sys.exit(1)
    print("\033[32mall schema checks passed\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
