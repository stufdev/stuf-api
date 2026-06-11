from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from run_supabase_sql import command_name, split_sql, strip_sql_comments, validate_safety


ENV_PATH = Path(__file__).resolve().parent / ".env"
DESTRUCTIVE_RE = re.compile(r"\b(drop|truncate)\b", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run SQL through Supabase REST/RPC public.stuf_exec_sql. "
            "Use this when direct Postgres ports are blocked."
        )
    )
    parser.add_argument("--file", required=True, help="SQL file to execute.")
    parser.add_argument("--readonly", action="store_true", help="Ask RPC to reject writes.")
    parser.add_argument("--allow-writes", action="store_true", help="Allow DDL/DML statements.")
    parser.add_argument("--allow-destructive", action="store_true", help="Allow DROP/TRUNCATE.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--max-rows", type=int, default=200)
    parser.add_argument("--rpc-name", default="stuf_exec_sql")
    return parser.parse_args()


def load_supabase_rest_config() -> tuple[str, str]:
    load_dotenv(ENV_PATH)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in stuf-api/.env")
    return url.rstrip("/"), key


def post_rpc(*, url: str, key: str, rpc_name: str, statement: str, readonly: bool, max_rows: int) -> Any:
    endpoint = f"{url}/rest/v1/rpc/{rpc_name}"
    headers = {
        "apikey": key,
        "authorization": f"Bearer {key}",
        "content-type": "application/json",
    }
    payload = {
        "sql": statement,
        "readonly": readonly,
        "max_rows": max_rows,
    }
    with httpx.Client(timeout=120.0) as client:
        response = client.post(endpoint, headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(
            f"RPC {rpc_name} failed HTTP {response.status_code}: {response.text}"
        )
    return response.json()


def run(args: argparse.Namespace) -> dict[str, Any]:
    sql_path = Path(args.file).resolve()
    raw_sql = sql_path.read_text(encoding="utf-8")
    statements = split_sql(strip_sql_comments(raw_sql))
    validate_safety(args, statements)
    if any(DESTRUCTIVE_RE.search(statement) for statement in statements) and not args.allow_destructive:
        raise RuntimeError("SQL contains DROP/TRUNCATE. Re-run with --allow-destructive if intentional.")

    readonly_rpc = args.readonly or not args.allow_writes
    summary: dict[str, Any] = {
        "file": str(sql_path),
        "statements": len(statements),
        "commands": [command_name(statement) for statement in statements],
        "readonly": readonly_rpc,
        "dry_run": args.dry_run,
        "transport": "supabase_rpc",
        "rpc_name": args.rpc_name,
        "results": [],
    }
    if args.dry_run:
        return summary

    url, key = load_supabase_rest_config()
    for index, statement in enumerate(statements, start=1):
        result = post_rpc(
            url=url,
            key=key,
            rpc_name=args.rpc_name,
            statement=statement,
            readonly=readonly_rpc,
            max_rows=args.max_rows,
        )
        summary["results"].append(
            {
                "index": index,
                "command": command_name(statement),
                "result": result,
            }
        )
    return summary


def print_human(summary: dict[str, Any]) -> None:
    print(f"file: {summary['file']}")
    print(f"statements: {summary['statements']}")
    print(f"transport: {summary['transport']}")
    print(f"readonly: {summary['readonly']}")
    if summary["dry_run"]:
        print("dry_run: true")
        print("commands:", ", ".join(summary["commands"]))
        return
    for item in summary["results"]:
        result = item["result"]
        print(f"\n[{item['index']}] {item['command']} rowcount={result.get('rowcount')} rows_returned={result.get('rows_returned')}")
        for row in result.get("rows") or []:
            print(json.dumps(row, ensure_ascii=False, default=str))


def main() -> None:
    args = parse_args()
    try:
        summary = run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    else:
        print_human(summary)


if __name__ == "__main__":
    main()
