from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parent / ".env"
DB_URL_ENV_NAMES = (
    "SUPABASE_DB_URL",
    "DATABASE_URL",
    "SUPABASE_POSTGRES_URL",
    "POSTGRES_URL",
    "DIRECT_URL",
)
DESTRUCTIVE_RE = re.compile(r"\b(drop|truncate)\b", re.IGNORECASE)
WRITE_RE = re.compile(
    r"\b(insert|update|delete|merge|alter|create|drop|truncate|grant|revoke|comment|vacuum|reindex|analyze)\b",
    re.IGNORECASE,
)
CONCURRENTLY_RE = re.compile(r"\bconcurrently\b", re.IGNORECASE)


@dataclass(frozen=True)
class StatementResult:
    index: int
    command: str
    rowcount: int
    columns: list[str]
    rows: list[dict[str, Any]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run SQL files against Supabase Postgres with direct DB connection. "
            "Use --readonly for validators and --allow-writes for migrations."
        )
    )
    parser.add_argument("--file", required=True, help="SQL file to execute.")
    parser.add_argument("--db-url-env", choices=DB_URL_ENV_NAMES, help="Specific env var to use.")
    parser.add_argument("--readonly", action="store_true", help="Run inside a read-only transaction.")
    parser.add_argument("--allow-writes", action="store_true", help="Allow DDL/DML statements.")
    parser.add_argument(
        "--allow-destructive",
        action="store_true",
        help="Allow DROP/TRUNCATE statements. Keep off unless explicitly intended.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and summarize SQL without executing.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary.")
    parser.add_argument("--statement-timeout-ms", type=int, default=120_000)
    parser.add_argument(
        "--no-transaction",
        action="store_true",
        help="Do not wrap in an explicit transaction. Required for CREATE INDEX CONCURRENTLY.",
    )
    parser.add_argument("--max-rows", type=int, default=200, help="Max rows to print per result set.")
    return parser.parse_args()


def require_psycopg() -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency psycopg. Run: python -m pip install -r requirements.txt"
        ) from exc
    return psycopg, dict_row


def load_db_url(args: argparse.Namespace) -> tuple[str, str]:
    load_dotenv(ENV_PATH)
    if args.db_url_env:
        value = os.getenv(args.db_url_env)
        if not value:
            raise RuntimeError(f"{args.db_url_env} is not set in environment or {ENV_PATH}.")
        return args.db_url_env, value

    for env_name in DB_URL_ENV_NAMES:
        value = os.getenv(env_name)
        if value:
            return env_name, value
    raise RuntimeError(
        "No direct Postgres URL found. Add one of these to stuf-api/.env: "
        + ", ".join(DB_URL_ENV_NAMES)
    )


def strip_sql_comments(sql: str) -> str:
    output: list[str] = []
    index = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    while index < len(sql):
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < len(sql) else ""
        if in_line_comment:
            if char in "\r\n":
                in_line_comment = False
                output.append(char)
            index += 1
            continue
        if in_block_comment:
            if char == "*" and nxt == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if not in_single and not in_double and char == "-" and nxt == "-":
            in_line_comment = True
            index += 2
            continue
        if not in_single and not in_double and char == "/" and nxt == "*":
            in_block_comment = True
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        output.append(char)
        index += 1
    return "".join(output)


def split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    index = 0
    in_single = False
    in_double = False
    dollar_tag: str | None = None
    while index < len(sql):
        char = sql[index]
        if dollar_tag:
            current.append(char)
            if sql.startswith(dollar_tag, index):
                current.extend(sql[index + 1 : index + len(dollar_tag)])
                index += len(dollar_tag)
                dollar_tag = None
                continue
            index += 1
            continue

        if not in_single and not in_double and char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
            if match:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                index += len(dollar_tag)
                continue

        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double

        if char == ";" and not in_single and not in_double:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
        index += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def command_name(statement: str) -> str:
    match = re.search(r"\b([A-Za-z]+)\b", statement)
    return (match.group(1).upper() if match else "UNKNOWN")


def validate_safety(args: argparse.Namespace, statements: list[str]) -> None:
    joined = "\n".join(statements)
    has_writes = bool(WRITE_RE.search(joined))
    has_destructive = bool(DESTRUCTIVE_RE.search(joined))
    if args.readonly and has_writes:
        raise RuntimeError("--readonly was requested, but SQL contains DDL/DML keywords.")
    if has_writes and not args.allow_writes:
        raise RuntimeError("SQL contains DDL/DML. Re-run with --allow-writes if this is intentional.")
    if has_destructive and not args.allow_destructive:
        raise RuntimeError("SQL contains DROP/TRUNCATE. Re-run with --allow-destructive if intentional.")


def redact_db_url(value: str) -> str:
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", value)


def run_sql(args: argparse.Namespace) -> dict[str, Any]:
    sql_path = Path(args.file).resolve()
    raw_sql = sql_path.read_text(encoding="utf-8")
    statements = split_sql(strip_sql_comments(raw_sql))
    validate_safety(args, statements)

    has_concurrently = any(CONCURRENTLY_RE.search(statement) for statement in statements)
    no_transaction = args.no_transaction or has_concurrently

    if args.dry_run:
        return {
            "file": str(sql_path),
            "statements": len(statements),
            "commands": [command_name(statement) for statement in statements],
            "dry_run": True,
            "readonly": args.readonly,
            "allow_writes": args.allow_writes,
            "no_transaction": no_transaction,
        }

    psycopg, dict_row = require_psycopg()
    env_name, db_url = load_db_url(args)
    results: list[StatementResult] = []
    with psycopg.connect(
        db_url,
        row_factory=dict_row,
        autocommit=no_transaction,
        prepare_threshold=None,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(f"set statement_timeout = {int(args.statement_timeout_ms)}")
            if args.readonly and not no_transaction:
                cur.execute("set transaction read only")
            for index, statement in enumerate(statements, start=1):
                cur.execute(statement)
                rows: list[dict[str, Any]] = []
                columns: list[str] = []
                if cur.description:
                    columns = [item.name for item in cur.description]
                    rows = [dict(row) for row in cur.fetchmany(args.max_rows)]
                results.append(
                    StatementResult(
                        index=index,
                        command=command_name(statement),
                        rowcount=cur.rowcount,
                        columns=columns,
                        rows=rows,
                    )
                )
        if not no_transaction:
            conn.commit()

    return {
        "file": str(sql_path),
        "db_url_env": env_name,
        "db_url": redact_db_url(db_url),
        "statements": len(statements),
        "readonly": args.readonly,
        "no_transaction": no_transaction,
        "results": [
            {
                "index": result.index,
                "command": result.command,
                "rowcount": result.rowcount,
                "columns": result.columns,
                "rows": result.rows,
            }
            for result in results
        ],
    }


def print_human(summary: dict[str, Any]) -> None:
    print(f"file: {summary['file']}")
    print(f"statements: {summary['statements']}")
    print(f"readonly: {summary.get('readonly', False)}")
    print(f"no_transaction: {summary.get('no_transaction', False)}")
    if summary.get("dry_run"):
        print("dry_run: true")
        print("commands:", ", ".join(summary["commands"]))
        return
    print(f"db_url_env: {summary['db_url_env']}")
    for result in summary["results"]:
        print(f"\n[{result['index']}] {result['command']} rowcount={result['rowcount']}")
        if result["columns"]:
            print("columns:", ", ".join(result["columns"]))
        for row in result["rows"]:
            print(json.dumps(row, ensure_ascii=False, default=str))


def main() -> None:
    args = parse_args()
    try:
        summary = run_sql(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    else:
        print_human(summary)


if __name__ == "__main__":
    main()
