"""Trigram indexes for the substring search path.

/api/search matches with ILIKE '%term%' over ~2.6M segments (see the note at
the top of api/search.py for why it is Postgres and not Meilisearch). That is
a sequential scan on every search, measured at 0.37-0.40s on production — fine
for humans clicking a search box, much less fine once agents are hitting it
through a public MCP endpoint, because each one costs a full scan of the table.

A GIN index with gin_trgm_ops is the standard fix, and the search module
already names it as the intended one. There is a catch that decides whether it
is worth building at all:

    pg_trgm extracts trigrams only from characters the database's locale
    considers word characters, and the PostgreSQL documentation says nothing
    about CJK either way. Whether 呱吉 produces usable trigrams on THIS
    database is an empirical question about its lc_ctype, not something that
    can be looked up.

Building the index blind would mean spending several minutes and 1-2 GB on a
structure the planner might never use. So `diagnose()` asks the database
first, and `build()` refuses to run when the answer is no.

Two entry points, both via ingest:

    python -m app.scripts.ingest --check-indexes   # report only, no writes
    python -m app.scripts.ingest --build-indexes   # verify, then build
"""

import time

from sqlalchemy import text

# Sample terms drawn from the real corpus: a name in every episode title, a
# common word, and the co-host's name that broke the Meilisearch path.
PROBE_TERMS = ["呱吉", "電腦", "采翎"]

# (index name, table, column). Both columns are matched with ILIKE by
# /api/search — segments.text for content, episodes.title for titles.
TRGM_INDEXES = [
    ("idx_segments_text_trgm", "segments", "text"),
    ("idx_episodes_title_trgm", "episodes", "title"),
]


def _is_postgres(engine) -> bool:
    return engine.dialect.name == "postgresql"


async def _probe(conn, report: dict, sql: str):
    """Run one read-only probe, recording failures instead of raising.

    A diagnostic that dies on its first unsupported query is worse than no
    diagnostic: `SHOW lc_ctype` is gone in PostgreSQL 16 and took the entire
    report with it, including the show_trgm() answer that is the only reason
    this command exists. Every probe is now individually survivable.
    """
    try:
        return (await conn.execute(text(sql))).scalar()
    except Exception as e:
        report.setdefault("notes", []).append(
            f"probe failed ({sql.split()[0]} ...): {type(e).__name__}: {e}"
        )
        return None


async def diagnose(engine) -> dict:
    """Report whether pg_trgm can usefully index this corpus.

    Returns a dict with a `usable` flag. Read-only — safe to run anytime.
    """
    report: dict = {"usable": False, "notes": []}

    if not _is_postgres(engine):
        report["notes"].append(f"Not PostgreSQL (dialect={engine.dialect.name}); nothing to do.")
        return report

    async with engine.connect() as conn:
        report["server_version"] = await _probe(conn, report, "SHOW server_version")

        # NOT `SHOW lc_ctype`. That GUC was removed in PostgreSQL 16 — the
        # locale is now only a property of the database row — so asking for it
        # raises UndefinedObjectError on any modern server and took the whole
        # report down with it. pg_database works on every version.
        ctype = await _probe(
            conn,
            report,
            "SELECT datctype FROM pg_database WHERE datname = current_database()",
        )
        report["lc_ctype"] = ctype
        report["lc_collate"] = await _probe(
            conn,
            report,
            "SELECT datcollate FROM pg_database WHERE datname = current_database()",
        )

        available = (
            await conn.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
            )
        ).scalar()
        installed = (
            await conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            )
        ).scalar()
        report["pg_trgm_available"] = bool(available)
        report["pg_trgm_installed"] = bool(installed)

        if not available:
            report["notes"].append(
                "pg_trgm is not available on this server. Nothing further can be done "
                "here; the alternative is a CJK-aware engine such as PGroonga."
            )
            return report

        # The decisive question. show_trgm needs the extension loaded, so
        # install it first — it is cheap, additive, and needed either way.
        if not installed:
            try:
                async with engine.begin() as write_conn:
                    await write_conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                report["pg_trgm_installed"] = True
                report["notes"].append("Installed pg_trgm extension.")
            except Exception as e:
                report["notes"].append(f"Could not CREATE EXTENSION pg_trgm: {e}")
                return report

    async with engine.connect() as conn:
        # Does Chinese text produce trigrams on this database at all?
        trigrams: dict[str, list[str]] = {}
        probe_failed = False
        for term in PROBE_TERMS:
            try:
                rows = (
                    await conn.execute(text("SELECT show_trgm(:t)"), {"t": term})
                ).scalar()
            except Exception as e:
                probe_failed = True
                report.setdefault("notes", []).append(
                    f"show_trgm({term}) failed: {type(e).__name__}: {e}"
                )
                rows = None
            trigrams[term] = list(rows or [])
        report["trigrams"] = trigrams

        # A term yields a usable index lookup only if it produces at least one
        # trigram. Empty output means the locale does not treat these
        # characters as word constituents and the index would never be probed.
        usable_terms = [t for t, g in trigrams.items() if g]
        # A failed probe is "unknown", not "unusable" — the difference decides
        # whether the answer is "do not build" or "this check is broken".
        report["usable"] = (
            not probe_failed and len(usable_terms) == len(PROBE_TERMS)
        )

        if not report["usable"] and not probe_failed:
            dead = [t for t, g in trigrams.items() if not g]
            report["notes"].append(
                f"show_trgm() returns nothing for {', '.join(dead)} — this database's "
                f"lc_ctype ({ctype}) does not treat these characters as word "
                "constituents, so a trigram index would never be used. Do NOT build it."
            )

        # Single characters cannot produce a complete trigram, so one-character
        # searches keep sequential-scanning no matter what is built.
        one_char = await _probe(conn, report, "SELECT show_trgm('吉')")
        report["single_char_trigrams"] = list(one_char or [])

        # Size context: a GIN trgm index over this much text is not small, and
        # Railway bills for the disk.
        try:
            size = (
                await conn.execute(
                    text("SELECT pg_size_pretty(pg_total_relation_size('segments'))")
                )
            ).scalar()
            report["segments_size"] = size
        except Exception:
            pass

        # A CREATE INDEX CONCURRENTLY that failed midway leaves an unusable
        # index behind that still costs writes. Surface it.
        try:
            invalid = (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_index i ON i.indexrelid = c.oid "
                        "WHERE NOT i.indisvalid"
                    )
                )
            ).scalars().all()
            report["invalid_indexes"] = list(invalid)
        except Exception as e:
            report.setdefault("notes", []).append(f"invalid-index scan failed: {e}")
            report["invalid_indexes"] = []

        try:
            existing = (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE indexname = ANY(:names)"
                    ),
                    {"names": [name for name, _, _ in TRGM_INDEXES]},
                )
            ).scalars().all()
            report["existing_indexes"] = list(existing)
        except Exception as e:
            report.setdefault("notes", []).append(f"existing-index scan failed: {e}")
            report["existing_indexes"] = []

    return report


async def benchmark(engine, term: str = "電腦") -> dict:
    """Time the real search count query and report which plan the planner picked."""
    if not _is_postgres(engine):
        return {}

    sql = (
        "SELECT episode_id, count(id) FROM segments "
        "WHERE text ILIKE :pattern GROUP BY episode_id"
    )
    async with engine.connect() as conn:
        started = time.monotonic()
        rows = (await conn.execute(text(sql), {"pattern": f"%{term}%"})).all()
        elapsed = time.monotonic() - started

        plan = (
            await conn.execute(
                text(f"EXPLAIN (FORMAT TEXT) {sql}"), {"pattern": f"%{term}%"}
            )
        ).scalars().all()

    plan_text = "\n".join(plan)
    return {
        "term": term,
        "seconds": round(elapsed, 3),
        "episodes_matched": len(rows),
        "uses_index": "Bitmap Index Scan" in plan_text or "Index Scan" in plan_text,
        "plan": plan_text,
    }


async def build(engine, force: bool = False) -> dict:
    """Create the trigram indexes, after checking they would actually be used.

    CREATE INDEX CONCURRENTLY so writes are not blocked — the nightly ingest
    should keep working through this. Concurrent builds cannot run inside a
    transaction, hence AUTOCOMMIT, and they take several minutes on 2.6M rows.
    """
    report = await diagnose(engine)

    if not _is_postgres(engine):
        return report

    if not report.get("usable") and not force:
        report["built"] = []
        report["notes"].append("Refusing to build. Re-run with --force to override.")
        return report

    built: list[str] = []
    errors: list[str] = []

    # AUTOCOMMIT: CONCURRENTLY is rejected inside a transaction block.
    autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit.connect() as conn:
        # A default statement_timeout would kill a multi-minute index build
        # partway through and leave an invalid index behind.
        await conn.execute(text("SET statement_timeout = 0"))

        for name, table, column in TRGM_INDEXES:
            if name in report.get("existing_indexes", []):
                report["notes"].append(f"{name} already exists; skipped.")
                continue
            started = time.monotonic()
            try:
                await conn.execute(
                    text(
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                        f"ON {table} USING gin ({column} gin_trgm_ops)"
                    )
                )
                built.append(f"{name} ({round(time.monotonic() - started, 1)}s)")
            except Exception as e:
                errors.append(f"{name}: {e}")

        # The planner needs fresh statistics to start choosing the new index.
        for _, table, _ in TRGM_INDEXES:
            try:
                await conn.execute(text(f"ANALYZE {table}"))
            except Exception as e:
                errors.append(f"ANALYZE {table}: {e}")

    report["built"] = built
    report["errors"] = errors
    return report


def print_report(report: dict, after: dict | None = None) -> None:
    """Human-readable summary for the CLI and the deployment log."""
    print("\n=== pg_trgm index report ===")
    for key in (
        "server_version",
        "lc_ctype",
        "lc_collate",
        "pg_trgm_available",
        "pg_trgm_installed",
        "segments_size",
        "existing_indexes",
        "invalid_indexes",
    ):
        if key in report:
            print(f"  {key:22}: {report[key]}")

    if "trigrams" in report:
        print("  trigram extraction:")
        for term, grams in report["trigrams"].items():
            shown = grams if grams else "*** NONE — index would be unusable ***"
            print(f"    {term:6} -> {shown}")
        single = report.get("single_char_trigrams")
        print(f"    single char 吉 -> {single if single else 'none (1-char searches stay seq scan)'}")

    print(f"  usable                : {report.get('usable')}")

    for note in report.get("notes", []):
        print(f"  ! {note}")
    for err in report.get("errors", []):
        print(f"  ERROR {err}")
    if report.get("built"):
        print(f"  built                 : {', '.join(report['built'])}")

    if after:
        print("\n=== benchmark ===")
        print(f"  term            : {after.get('term')}")
        print(f"  seconds         : {after.get('seconds')}")
        print(f"  episodes matched: {after.get('episodes_matched')}")
        print(f"  uses index      : {after.get('uses_index')}")
        print("  plan:")
        for line in (after.get("plan") or "").splitlines():
            print(f"    {line}")
    print()
