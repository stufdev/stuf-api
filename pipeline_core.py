from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import httpx
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

BASE_URL = "https://v3.football.api-sports.io"
FINAL_STATUSES = {"FT", "AET", "PEN"}
UPCOMING_STATUSES = {"NS", "TBD"}
NON_PLAYED_STATUSES = {"PST", "CANC", "ABD", "AWD", "WO", "SUSP", "INT"}
SUPPORTED_LEAGUE_FEATURE_COLUMNS = {
    "comparison": "enabled_for_comparison",
    "fixtures": "enabled_for_fixtures",
    "pipeline": "enabled_for_pipeline",
    "streaks": "enabled_for_streaks",
}


def configure_logging(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    return logging.getLogger(name)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def chunked(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def dedupe_positive_ints(values: Iterable[Any]) -> tuple[int, ...]:
    parsed: list[int] = []
    for raw_value in values:
        parsed_value = int(raw_value)
        if parsed_value <= 0:
            continue
        if parsed_value not in parsed:
            parsed.append(parsed_value)
    return tuple(parsed)


def parse_target_leagues(raw_value: str | None) -> tuple[int, ...]:
    if not raw_value:
        return ()

    return dedupe_positive_ints(
        part.strip()
        for part in raw_value.split(",")
        if part.strip()
    )


def supports_first_half_statistics(season: Any) -> bool:
    parsed_season = parse_optional_int(season)
    return parsed_season is not None and parsed_season >= 2024


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return any(safe_bool(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(safe_bool(item) for item in value)
    return bool(value)


def normalize_league_type(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip().lower()
    if normalized in {"league", "cup"}:
        return normalized
    return None


def normalize_name(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def strip_country_suffix(value: str | None) -> str:
    raw = " ".join((value or "").strip().split())
    if not raw:
        return ""
    return raw.split(",", 1)[0].strip()


def is_abbreviated_referee_name(value: str | None) -> bool:
    normalized = strip_country_suffix(value)
    if not normalized:
        return False
    tokens = re.findall(r"[a-z]+", normalized.lower())
    if len(tokens) <= 1:
        return True
    return any(len(token) == 1 for token in tokens[:-1])


def build_referee_alias_key(value: str | None) -> str:
    normalized = strip_country_suffix(value).lower()
    if not normalized:
        return ""
    tokens = re.findall(r"[a-z]+", normalized)
    if not tokens:
        return ""
    first_initial = tokens[0][0]
    last_token = tokens[-1]
    return f"{first_initial} {last_token}"


def choose_preferred_referee_name(existing_name: str | None, incoming_name: str | None) -> str:
    existing = " ".join((existing_name or "").strip().split())
    incoming = " ".join((incoming_name or "").strip().split())
    if not existing:
        return incoming
    if not incoming:
        return existing
    existing_base = strip_country_suffix(existing)
    incoming_base = strip_country_suffix(incoming)
    existing_abbrev = is_abbreviated_referee_name(existing_base)
    incoming_abbrev = is_abbreviated_referee_name(incoming_base)
    if existing_abbrev and not incoming_abbrev:
        return incoming
    if incoming_abbrev and not existing_abbrev:
        return existing
    return incoming if len(incoming_base) > len(existing_base) else existing


def parse_percent(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def sum_optional_ints(*values: Any) -> int | None:
    parsed_values: list[int] = []
    for value in values:
        parsed = parse_optional_int(value)
        if parsed is None:
            return None
        parsed_values.append(parsed)
    return sum(parsed_values)


def subtract_optional_ints(total: Any, partial: Any) -> int | None:
    parsed_total = parse_optional_int(total)
    parsed_partial = parse_optional_int(partial)
    if parsed_total is None or parsed_partial is None:
        return None

    delta = parsed_total - parsed_partial
    if delta < 0:
        return None
    return delta


def normalize_optional_fk_id(value: Any) -> int | None:
    parsed = parse_int(value, 0)
    return parsed if parsed > 0 else None


def normalize_player_pass_accuracy(total_passes: Any, raw_accuracy: Any) -> float | None:
    parsed_total = parse_int(total_passes, 0)
    parsed_accuracy = parse_percent(raw_accuracy)
    if parsed_accuracy is None:
        return None

    if parsed_accuracy > 100 and parsed_total > 0:
        return round((parsed_accuracy / parsed_total) * 100, 2)

    return parsed_accuracy


ALLOWED_FIXTURE_EVENT_TYPES = {
    "Goal",
    "Card",
    "subst",
    "Var",
    "Penalty",
    "Missed Penalty",
    "Own Goal",
    "Other",
}


def normalize_fixture_event_type(raw_type: Any, detail: Any) -> str:
    normalized_type = str(raw_type or "").strip()
    normalized_detail = str(detail or "").strip().lower()

    if normalized_type in ALLOWED_FIXTURE_EVENT_TYPES:
        return normalized_type

    if normalized_type == "Goal":
        if normalized_detail == "own goal":
            return "Own Goal"
        if normalized_detail == "missed penalty":
            return "Missed Penalty"
        if normalized_detail == "penalty":
            return "Penalty"
        return "Goal"

    if normalized_detail == "own goal":
        return "Own Goal"
    if normalized_detail == "missed penalty":
        return "Missed Penalty"
    if normalized_detail == "penalty":
        return "Penalty"

    return "Other"


@dataclass(frozen=True)
class Settings:
    api_key: str
    supabase_url: str
    supabase_service_role_key: str
    target_leagues: tuple[int, ...]
    pinnacle_bookmaker_name: str


def load_settings() -> Settings:
    api_key = os.getenv("API_SPORTS_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    pinnacle_bookmaker_name = os.getenv("PINNACLE_BOOKMAKER_NAME", "Pinnacle")

    if not all([api_key, supabase_url, supabase_service_role_key]):
        raise RuntimeError(
            "Faltan variables de entorno obligatorias: API_SPORTS_KEY, SUPABASE_URL y "
            "SUPABASE_SERVICE_ROLE_KEY (o SUPABASE_KEY)."
        )

    return Settings(
        api_key=api_key,
        supabase_url=supabase_url,
        supabase_service_role_key=supabase_service_role_key,
        target_leagues=parse_target_leagues(os.getenv("TARGET_LEAGUES")),
        pinnacle_bookmaker_name=pinnacle_bookmaker_name,
    )


def create_supabase_client(settings: Settings) -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


class ApiFootballClient:
    def __init__(self, settings: Settings, logger: logging.Logger, request_delay_seconds: float = 0.0):
        self._settings = settings
        self._logger = logger
        self._client: httpx.AsyncClient | None = None
        self._request_delay_seconds = max(0.0, request_delay_seconds)
        self._last_request_at = 0.0
        self._request_lock = asyncio.Lock()

    async def __aenter__(self) -> "ApiFootballClient":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _wait_for_request_slot(self) -> None:
        if self._request_delay_seconds <= 0:
            return

        async with self._request_lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_request_at
            wait_seconds = self._request_delay_seconds - elapsed
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._last_request_at = loop.time()

    async def fetch(self, endpoint: str, params: dict[str, Any] | None = None, retries: int = 4) -> dict[str, Any] | None:
        if self._client is None:
            raise RuntimeError("ApiFootballClient debe usarse dentro de 'async with'.")

        url = f"{BASE_URL}/{endpoint}"
        headers = {"x-apisports-key": self._settings.api_key}

        for attempt in range(1, retries + 1):
            try:
                await self._wait_for_request_slot()
                response = await self._client.get(url, headers=headers, params=params)
            except Exception as exc:
                if attempt == retries:
                    self._logger.error("Excepcion en %s con params=%s: %s", endpoint, params, exc)
                    return None
                await asyncio.sleep(2 * attempt)
                continue

            daily_remaining = response.headers.get("x-ratelimit-requests-remaining")
            minute_remaining = response.headers.get("X-RateLimit-Remaining")
            if minute_remaining is not None and minute_remaining.isdigit():
                minute_remaining_count = int(minute_remaining)
                if minute_remaining_count <= 1:
                    self._logger.warning(
                        "Rate-limit del minuto casi agotado en %s. Minuto restante=%s, diario restante=%s. Pausa 60s.",
                        endpoint,
                        minute_remaining,
                        daily_remaining,
                    )
                    await asyncio.sleep(60)
                elif minute_remaining_count < 5:
                    self._logger.warning(
                        "Rate-limit del minuto bajo en %s. Minuto restante=%s, diario restante=%s. Pausa 10s.",
                        endpoint,
                        minute_remaining,
                        daily_remaining,
                    )
                    await asyncio.sleep(10)
                elif minute_remaining_count < 20:
                    self._logger.warning(
                        "Presion de rate-limit en %s. Minuto restante=%s, diario restante=%s",
                        endpoint,
                        minute_remaining,
                        daily_remaining,
                    )

            if response.status_code == 429:
                wait_seconds = min(12, 2 * attempt + 2)
                self._logger.warning("429 en %s params=%s. Esperando %ss...", endpoint, params, wait_seconds)
                await asyncio.sleep(wait_seconds)
                continue

            if response.status_code in {499, 500}:
                wait_seconds = min(10, 2 * attempt)
                self._logger.warning(
                    "Error transitorio %s en %s params=%s. Reintentando en %ss...",
                    response.status_code,
                    endpoint,
                    params,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue

            if response.status_code != 200:
                self._logger.error("Error %s en %s params=%s: %s", response.status_code, endpoint, params, response.text)
                return None

            payload = response.json()
            errors = payload.get("errors")
            if errors:
                error_message = str(errors)
                if "Too many requests" in error_message or "rateLimit" in error_message:
                    wait_seconds = min(12, 2 * attempt + 2)
                    self._logger.warning("Rate limit en JSON de %s. Esperando %ss...", endpoint, wait_seconds)
                    await asyncio.sleep(wait_seconds)
                    continue

                self._logger.error("Error logico en %s params=%s: %s", endpoint, params, errors)
                return None

            return payload

        self._logger.error("Reintentos agotados para %s params=%s", endpoint, params)
        return None

    async def fetch_paginated(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        page_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query_params = dict(params or {})
        page = 1
        total_pages = 1
        items: list[dict[str, Any]] = []

        while page <= total_pages:
            query_params["page"] = page
            payload = await self.fetch(endpoint, query_params)
            if not payload:
                break

            response_items = payload.get("response", [])
            if isinstance(response_items, list):
                items.extend(response_items)

            paging = payload.get("paging") or {}
            total_pages = int(paging.get("total") or 1)
            if page_limit is not None:
                total_pages = min(total_pages, page_limit)

            page += 1

        return items


class StufRepository:
    def __init__(self, supabase: Client, logger: logging.Logger):
        self.supabase = supabase
        self.logger = logger

    def _execute(self, request_factory, operation: str, *, attempts: int = 4, base_delay: float = 0.75):
        for attempt in range(1, attempts + 1):
            try:
                return request_factory().execute()
            except httpx.TransportError as exc:
                if attempt >= attempts:
                    raise

                wait_seconds = base_delay * attempt
                self.logger.warning(
                    "Supabase %s fallo (intento %s/%s). Reintentando en %.2fs. Error: %s",
                    operation,
                    attempt,
                    attempts,
                    wait_seconds,
                    exc,
                )
                time.sleep(wait_seconds)

    def _delete_scope_rows(
        self,
        table_name: str,
        filters: dict[str, Any],
        operation: str,
        *,
        older_than: str | None = None,
    ) -> None:
        def request():
            query = self.supabase.table(table_name).delete()
            for key, value in filters.items():
                query = query.eq(key, value)
            if older_than is not None:
                query = query.lt("updated_at", older_than)
            return query

        self._execute(request, operation)

    def _upsert_rows(
        self,
        table_name: str,
        rows: list[dict[str, Any]],
        on_conflict: str,
        operation: str,
        *,
        batch_size: int = 500,
    ) -> None:
        for batch in chunked(rows, batch_size):
            self._execute(
                lambda batch=batch: self.supabase.table(table_name).upsert(list(batch), on_conflict=on_conflict),
                f"{operation} batch={len(batch)}",
            )

    def get_supported_league_ids(
        self,
        *,
        feature: str = "pipeline",
        season: int | None = None,
    ) -> tuple[int, ...]:
        flag_column = SUPPORTED_LEAGUE_FEATURE_COLUMNS.get(feature)
        if flag_column is None:
            raise ValueError(f"Unsupported league feature '{feature}'.")

        def request():
            query = (
                self.supabase.table("supported_leagues")
                .select("league_id, display_order")
                .eq("is_active", True)
                .eq(flag_column, True)
                .order("display_order", desc=False)
                .order("league_id", desc=False)
            )
            if season is not None:
                query = query.eq("season", season)
            return query

        response = self._execute(
            request,
            f"load supported leagues feature={feature} season={season or 'ALL'}",
        )
        rows = response.data or []
        return dedupe_positive_ints(
            row.get("league_id")
            for row in rows
            if row.get("league_id") is not None
        )

    def upsert_league_catalog_entry(self, league_entry: dict[str, Any]) -> None:
        league = league_entry.get("league") or {}
        country = league_entry.get("country") or {}
        seasons = league_entry.get("seasons") or []

        if not league.get("id"):
            return

        country_code = country.get("code")
        if country_code:
            self._execute(
                lambda: self.supabase.table("countries").upsert(
                    {
                        "code": country_code,
                        "name": country.get("name") or country_code,
                        "flag_url": country.get("flag"),
                        "updated_at": utcnow().isoformat(),
                    }
                ),
                "upsert country catalog",
            )

        self._execute(
            lambda: self.supabase.table("leagues").upsert(
                {
                    "id": league["id"],
                    "name": league.get("name"),
                    "type": normalize_league_type(league.get("type")),
                    "logo_url": league.get("logo"),
                    "country_name": country.get("name"),
                    "country_code": country_code,
                    "raw_payload": league_entry,
                    "updated_at": utcnow().isoformat(),
                }
            ),
            "upsert leagues catalog",
        )

        season_rows = []
        coverage_rows = []
        for season_entry in seasons:
            season = season_entry.get("year")
            if season is None:
                continue

            season_rows.append(
                {
                    "league_id": league["id"],
                    "season": season,
                    "start_date": season_entry.get("start"),
                    "end_date": season_entry.get("end"),
                    "is_current": bool(season_entry.get("current")),
                    "updated_at": utcnow().isoformat(),
                }
            )

            coverage = season_entry.get("coverage") or {}
            fixtures_coverage = coverage.get("fixtures") or {}

            coverage_rows.append(
                {
                    "league_id": league["id"],
                    "season": season,
                    "fixtures_events": safe_bool(fixtures_coverage.get("events")),
                    "fixtures_lineups": safe_bool(fixtures_coverage.get("lineups")),
                    "fixtures_statistics": safe_bool(fixtures_coverage.get("statistics_fixtures")),
                    "fixtures_players_statistics": safe_bool(fixtures_coverage.get("statistics_players")),
                    "standings": safe_bool(coverage.get("standings")),
                    "players": safe_bool(coverage.get("players")),
                    "top_scorers": safe_bool(coverage.get("top_scorers")),
                    "top_assists": safe_bool(coverage.get("top_assists")),
                    "top_cards": safe_bool(coverage.get("top_cards")),
                    "injuries": safe_bool(coverage.get("injuries")),
                    "predictions": safe_bool(coverage.get("predictions")),
                    "odds": safe_bool(coverage.get("odds")),
                    "raw_payload": coverage,
                    "updated_at": utcnow().isoformat(),
                }
            )

        if season_rows:
            self._execute(
                lambda: self.supabase.table("league_seasons").upsert(
                    season_rows,
                    on_conflict="league_id,season",
                ),
                "upsert league seasons",
            )

        if coverage_rows:
            self._execute(
                lambda: self.supabase.table("league_coverage").upsert(
                    coverage_rows,
                    on_conflict="league_id,season",
                ),
                "upsert league coverage",
            )

    def sync_bookmakers(self, bookmakers: list[dict[str, Any]]) -> None:
        rows = []
        for bookmaker in bookmakers:
            if not bookmaker.get("id"):
                continue

            bookmaker_name = bookmaker.get("name") or bookmaker.get("label") or bookmaker.get("value")
            if bookmaker_name is None:
                self.logger.warning("Se omite bookmaker sin nombre: %s", bookmaker)
                continue

            rows.append(
                {
                    "id": bookmaker["id"],
                    "name": str(bookmaker_name),
                    "raw_payload": bookmaker,
                    "updated_at": utcnow().isoformat(),
                }
            )

        if rows:
            self._execute(
                lambda: self.supabase.table("api_reference_bookmakers").upsert(rows),
                "upsert bookmakers catalog",
            )

    def sync_bets(self, scope: str, bets: list[dict[str, Any]]) -> None:
        rows = []
        for bet in bets:
            if not bet.get("id"):
                continue

            bet_name = bet.get("name") or bet.get("label") or bet.get("value")
            if bet_name is None:
                self.logger.warning("Se omite bet sin nombre en scope=%s: %s", scope, bet)
                continue

            rows.append(
                {
                    "id": bet["id"],
                    "market_scope": scope,
                    "name": str(bet_name),
                    "raw_payload": bet,
                    "updated_at": utcnow().isoformat(),
                }
            )

        if rows:
            self._execute(
                lambda: self.supabase.table("api_reference_bets").upsert(rows, on_conflict="id,market_scope"),
                "upsert bets catalog",
            )

    def upsert_market_definitions(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        now = utcnow().isoformat()
        payload = [
            {
                "value_source": "team_fixture_facts",
                "expression": {},
                "is_active": True,
                "updated_at": now,
                **row,
            }
            for row in rows
        ]
        self._execute(
            lambda: self.supabase.table("market_definitions").upsert(payload, on_conflict="key"),
            f"upsert market definitions batch={len(payload)}",
        )

    def upsert_referee_from_name(
        self,
        name: str | None,
        country_name: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> int | None:
        referee_name = " ".join((name or "").strip().split())
        if not referee_name:
            return None

        normalized = normalize_name(referee_name)
        alias_key = build_referee_alias_key(referee_name)
        existing = self._execute(
            lambda: self.supabase.table("referees")
            .select("id,name,name_normalized,country_name")
            .eq("name_normalized", normalized)
            .limit(1),
            f"load referee name={referee_name}",
        )
        rows = existing.data or []
        if alias_key:
            candidates = self._execute(
                lambda: self.supabase.table("referees").select("id,name,name_normalized,country_name"),
                f"load referee candidates alias={alias_key}",
            )
            candidate_rows = candidates.data or []
            alias_matches = [
                row
                for row in candidate_rows
                if build_referee_alias_key(row.get("name_normalized") or row.get("name")) == alias_key
            ]
            if country_name:
                country_matches = [
                    row
                    for row in alias_matches
                    if normalize_name(row.get("country_name")) == normalize_name(country_name)
                ]
                if country_matches:
                    alias_matches = country_matches
            if rows:
                current_row = rows[0]
                richer_aliases = [row for row in alias_matches if not is_abbreviated_referee_name(row.get("name"))]
                if is_abbreviated_referee_name(current_row.get("name")) and richer_aliases:
                    rows = sorted(
                        richer_aliases,
                        key=lambda row: len(strip_country_suffix(row.get("name"))),
                        reverse=True,
                    )[:1]
            elif len(alias_matches) == 1:
                rows = alias_matches
            elif len(alias_matches) > 1:
                non_abbreviated = [row for row in alias_matches if not is_abbreviated_referee_name(row.get("name"))]
                preferred_pool = non_abbreviated or alias_matches
                rows = sorted(
                    preferred_pool,
                    key=lambda row: len(strip_country_suffix(row.get("name"))),
                    reverse=True,
                )[:1]
        if rows:
            existing_row = rows[0]
            referee_id = existing_row.get("id")
            update_payload: dict[str, Any] = {
                "name": choose_preferred_referee_name(existing_row.get("name"), referee_name),
                "updated_at": utcnow().isoformat(),
            }
            if country_name:
                update_payload["country_name"] = country_name
            if raw_payload:
                update_payload["raw_payload"] = raw_payload
            self._execute(
                lambda: self.supabase.table("referees").update(update_payload).eq("id", referee_id),
                f"update referee name={referee_name}",
            )
            return int(referee_id)

        insert_payload = {
            "name": referee_name,
            "name_normalized": normalized,
            "country_name": country_name,
            "raw_payload": raw_payload or {},
            "updated_at": utcnow().isoformat(),
        }
        created = self._execute(
            lambda: self.supabase.table("referees").insert(insert_payload),
            f"insert referee name={referee_name}",
        )
        created_rows = created.data or []
        if created_rows and created_rows[0].get("id") is not None:
            return int(created_rows[0]["id"])

        reread = self._execute(
            lambda: self.supabase.table("referees")
            .select("id")
            .eq("name_normalized", normalized)
            .limit(1),
            f"reload referee name={referee_name}",
        )
        reread_rows = reread.data or []
        return int(reread_rows[0]["id"]) if reread_rows else None

    def load_coverage_map(self) -> dict[tuple[int, int], dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("league_coverage").select("*"),
            "load coverage map",
        )
        rows = response.data or []
        return {(row["league_id"], row["season"]): row for row in rows}

    def get_bookmakers(self) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("api_reference_bookmakers").select("*"),
            "load bookmakers",
        )
        return response.data or []

    def get_prediction_row(self, fixture_id: int) -> dict[str, Any] | None:
        response = self._execute(
            lambda: self.supabase.table("fixture_predictions_api")
            .select("*")
            .eq("fixture_id", fixture_id)
            .limit(1),
            f"load prediction row fixture={fixture_id}",
        )
        rows = response.data or []
        return rows[0] if rows else None

    def has_lineups(self, fixture_id: int) -> bool:
        response = self._execute(
            lambda: self.supabase.table("fixture_lineups")
            .select("fixture_id")
            .eq("fixture_id", fixture_id)
            .limit(1),
            f"check lineups fixture={fixture_id}",
        )
        return bool(response.data)

    def get_fixture_rows(self, fixture_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        if not fixture_ids:
            return {}

        rows: dict[int, dict[str, Any]] = {}
        for batch in chunked(list(dict.fromkeys(fixture_ids)), 150):
            response = self._execute(
                lambda batch=batch: self.supabase.table("fixtures")
                .select(
                    "id,status_short,date,league_id,season,"
                    "hydrated_statistics,hydrated_events,hydrated_players,hydrated_predictions,hydrated_lineups"
                )
                .in_("id", list(batch)),
                f"load fixture rows batch={len(batch)}",
            )
            for row in response.data or []:
                rows[row["id"]] = row
        return rows

    def get_fixture_detail_health(self, fixture_id: int) -> dict[str, Any]:
        fixture_row = self.get_fixture_rows([fixture_id]).get(fixture_id, {})
        stats_response = self._execute(
            lambda: self.supabase.table("fixture_statistics")
            .select("team_id,period")
            .eq("fixture_id", fixture_id)
            .eq("period", "FT"),
            f"load fixture stats health fixture={fixture_id}",
        )
        players_response = self._execute(
            lambda: self.supabase.table("player_fixture_stats")
            .select("player_id")
            .eq("fixture_id", fixture_id)
            .limit(1),
            f"load fixture players health fixture={fixture_id}",
        )
        prediction = self.get_prediction_row(fixture_id)

        return {
            "has_stats": len(stats_response.data or []) >= 2,
            "has_events": bool(fixture_row.get("hydrated_events")),
            "has_players": bool(players_response.data),
            "has_prediction": prediction is not None or bool(fixture_row.get("hydrated_predictions")),
        }

    def get_finished_fixture_skip_map(
        self,
        fixture_ids: Sequence[int],
        require_events: bool = True,
        require_players: bool = True,
        require_prediction: bool = True,
    ) -> dict[int, bool]:
        unique_ids = list(dict.fromkeys(fixture_ids))
        if not unique_ids:
            return {}

        fixture_rows = self.get_fixture_rows(unique_ids)
        completed_ids = [
            fixture_id
            for fixture_id, row in fixture_rows.items()
            if is_final_status(row.get("status_short"))
        ]
        if not completed_ids:
            return {fixture_id: False for fixture_id in unique_ids}

        stats_response = self._execute(
            lambda: self.supabase.table("fixture_statistics")
            .select("fixture_id,team_id")
            .in_("fixture_id", completed_ids)
            .eq("period", "FT"),
            f"batch stats health size={len(completed_ids)}",
        )
        stats_counts: dict[int, int] = {}
        for row in stats_response.data or []:
            fixture_id = row.get("fixture_id")
            if fixture_id is None:
                continue
            stats_counts[fixture_id] = stats_counts.get(fixture_id, 0) + 1

        player_fixture_ids: set[int] = set()
        if require_players:
            for batch in chunked(completed_ids, 25):
                offset = 0
                while True:
                    players_response = self._execute(
                        lambda batch=batch, offset=offset: self.supabase.table("player_fixture_stats")
                        .select("fixture_id")
                        .in_("fixture_id", batch)
                        .range(offset, offset + 999),
                        f"batch players health size={len(batch)} offset={offset}",
                    )
                    batch_rows = players_response.data or []
                    player_fixture_ids.update(
                        row.get("fixture_id") for row in batch_rows if row.get("fixture_id") is not None
                    )
                    if len(batch_rows) < 1000:
                        break
                    offset += 1000

        prediction_fixture_ids: set[int] = set()
        if require_prediction:
            prediction_response = self._execute(
                lambda: self.supabase.table("fixture_predictions_api")
                .select("fixture_id")
                .in_("fixture_id", completed_ids),
                f"batch predictions health size={len(completed_ids)}",
            )
            prediction_fixture_ids = {
                row.get("fixture_id") for row in (prediction_response.data or []) if row.get("fixture_id") is not None
            }

        return {
            fixture_id: (
                fixture_id in fixture_rows
                and is_final_status((fixture_rows.get(fixture_id) or {}).get("status_short"))
                and stats_counts.get(fixture_id, 0) >= 2
                and (not require_events or bool((fixture_rows.get(fixture_id) or {}).get("hydrated_events")))
                and (
                    not require_players
                    or fixture_id in player_fixture_ids
                )
                and (
                    not require_prediction
                    or fixture_id in prediction_fixture_ids
                    or bool((fixture_rows.get(fixture_id) or {}).get("hydrated_predictions"))
                )
            )
            for fixture_id in unique_ids
        }

    def historical_backfill_satisfied(
        self,
        league_id: int,
        season: int,
        limit: int,
        require_events: bool = True,
        require_players: bool = True,
        require_prediction: bool = True,
    ) -> bool:
        if limit <= 0:
            return True

        response = self._execute(
            lambda: self.supabase.table("fixtures")
            .select("id")
            .eq("league_id", league_id)
            .eq("season", season)
            .in_("status_short", sorted(FINAL_STATUSES))
            .order("date", desc=True)
            .limit(limit),
            f"check historical backfill coverage league={league_id} season={season} limit={limit}",
        )
        rows = response.data or []
        if len(rows) < limit:
            return False

        fixture_ids = [row.get("id") for row in rows if row.get("id") is not None]
        if len(fixture_ids) < limit:
            return False

        skip_map = self.get_finished_fixture_skip_map(
            fixture_ids,
            require_events=require_events,
            require_players=require_players,
            require_prediction=require_prediction,
        )
        return all(skip_map.get(fixture_id, False) for fixture_id in fixture_ids)

    def upsert_fixture_shell(self, fixture_payload: dict[str, Any]) -> None:
        fixture = fixture_payload.get("fixture") or {}
        league = fixture_payload.get("league") or {}
        teams = fixture_payload.get("teams") or {}
        goals = fixture_payload.get("goals") or {}
        score = fixture_payload.get("score") or {}

        home_team = teams.get("home") or {}
        away_team = teams.get("away") or {}

        if not fixture.get("id"):
            return

        self._execute(
            lambda: self.supabase.table("leagues").upsert(
                {
                    "id": league.get("id"),
                    "name": league.get("name"),
                    "type": normalize_league_type(league.get("type")),
                    "logo_url": league.get("logo"),
                    "country_name": league.get("country"),
                    "country_code": None,
                    "raw_payload": league,
                    "updated_at": utcnow().isoformat(),
                }
            ),
            f"upsert fixture shell league fixture={fixture['id']}",
        )

        self._execute(
            lambda: self.supabase.table("league_seasons").upsert(
                {
                    "league_id": league.get("id"),
                    "season": league.get("season"),
                    "updated_at": utcnow().isoformat(),
                },
                on_conflict="league_id,season",
            ),
            f"upsert fixture shell league season fixture={fixture['id']}",
        )

        venue = fixture.get("venue") or {}
        if venue.get("id"):
            self._execute(
                lambda: self.supabase.table("venues").upsert(
                    {
                        "id": venue.get("id"),
                        "name": venue.get("name"),
                        "city": venue.get("city"),
                        "raw_payload": venue,
                        "updated_at": utcnow().isoformat(),
                    }
                ),
                f"upsert venue fixture={fixture['id']} venue={venue.get('id')}",
            )

        for team in (home_team, away_team):
            if not team.get("id"):
                continue
            self._execute(
                lambda team=team: self.supabase.table("teams").upsert(
                    {
                        "id": team["id"],
                        "name": team.get("name"),
                        "logo_url": team.get("logo"),
                    }
                ),
                f"upsert team fixture={fixture['id']} team={team['id']}",
            )

        team_season_rows = [
            {
                "team_id": team.get("id"),
                "league_id": league.get("id"),
                "season": league.get("season"),
                "source": "fixtures",
                "updated_at": utcnow().isoformat(),
            }
            for team in (home_team, away_team)
            if team.get("id") and league.get("id") and league.get("season")
        ]
        if team_season_rows:
            self._execute(
                lambda: self.supabase.table("team_league_seasons").upsert(
                    team_season_rows,
                    on_conflict="team_id,league_id,season",
                ),
                f"upsert team league seasons fixture={fixture['id']}",
            )

        referee_name = fixture.get("referee")
        referee_id = self.upsert_referee_from_name(
            referee_name,
            country_name=league.get("country"),
            raw_payload=fixture_payload,
        )
        venue_id = normalize_optional_fk_id((fixture.get("venue") or {}).get("id"))
        fixture_row = {
            "id": fixture["id"],
            "date": fixture.get("date"),
            "league_id": league.get("id"),
            "season": league.get("season"),
            "round_name": league.get("round"),
            "timestamp_unix": fixture.get("timestamp"),
            "timezone": fixture.get("timezone"),
            "venue_id": venue_id,
            "venue_name_raw": (fixture.get("venue") or {}).get("name"),
            "venue_city_raw": (fixture.get("venue") or {}).get("city"),
            "referee_name_raw": referee_name,
            "home_team_id": home_team.get("id"),
            "away_team_id": away_team.get("id"),
            "home_goals": goals.get("home"),
            "away_goals": goals.get("away"),
            "home_goals_1h": (score.get("halftime") or {}).get("home"),
            "away_goals_1h": (score.get("halftime") or {}).get("away"),
            "status_short": (fixture.get("status") or {}).get("short"),
            "status_long": (fixture.get("status") or {}).get("long"),
            "elapsed": (fixture.get("status") or {}).get("elapsed"),
            "raw_payload": fixture_payload,
            "updated_at": utcnow().isoformat(),
        }
        if referee_id is not None:
            fixture_row["referee_id"] = referee_id

        self._execute(
            lambda: self.supabase.table("fixtures").upsert(fixture_row),
            f"upsert fixture shell fixture={fixture['id']}",
        )

    def mark_fixture_hydration(self, fixture_id: int, **flags: bool) -> None:
        allowed_flags = {
            "hydrated_statistics",
            "hydrated_events",
            "hydrated_players",
            "hydrated_lineups",
            "hydrated_predictions",
            "hydrated_odds",
        }
        payload = {key: bool(value) for key, value in flags.items() if key in allowed_flags}
        if not payload:
            return

        payload["updated_at"] = utcnow().isoformat()
        self._execute(
            lambda: self.supabase.table("fixtures").update(payload).eq("id", fixture_id),
            f"mark fixture hydration fixture={fixture_id}",
        )

    def replace_fixture_statistics(
        self,
        fixture_id: int,
        full_time_statistics: list[dict[str, Any]],
        first_half_statistics: list[dict[str, Any]] | None = None,
    ) -> None:
        def build_snapshot_map(
            team_statistics: list[dict[str, Any]],
            statistics_key: str = "statistics",
        ) -> dict[int, dict[str, Any]]:
            snapshots: dict[int, dict[str, Any]] = {}
            for team_stat in team_statistics:
                team = team_stat.get("team") or {}
                team_id = team.get("id")
                if not team_id:
                    continue

                statistics_items = team_stat.get(statistics_key) or []
                if not statistics_items:
                    continue

                snapshots[team_id] = {
                    "raw_payload": team_stat,
                    "stats": {
                        item.get("type"): item.get("value")
                        for item in statistics_items
                        if item.get("type")
                    },
                }
            return snapshots

        def build_period_row(team_id: int, period: str, stats_dict: dict[str, Any], raw_payload: Any) -> dict[str, Any]:
            return {
                "fixture_id": fixture_id,
                "team_id": team_id,
                "period": period,
                "shots_on_target": parse_optional_int(stats_dict.get("Shots on Goal")),
                "shots_off_target": parse_optional_int(stats_dict.get("Shots off Goal")),
                "total_shots": parse_optional_int(stats_dict.get("Total Shots")),
                "blocked_shots": parse_optional_int(stats_dict.get("Blocked Shots")),
                "shots_inside_box": parse_optional_int(stats_dict.get("Shots insidebox")),
                "shots_outside_box": parse_optional_int(stats_dict.get("Shots outsidebox")),
                "fouls": parse_optional_int(stats_dict.get("Fouls")),
                "corners": parse_optional_int(stats_dict.get("Corner Kicks")),
                "offsides": parse_optional_int(stats_dict.get("Offsides")),
                "yellow_cards": parse_optional_int(stats_dict.get("Yellow Cards")),
                "red_cards": parse_optional_int(stats_dict.get("Red Cards")),
                "booking_points": None,
                "goalkeeper_saves": parse_optional_int(stats_dict.get("Goalkeeper Saves")),
                "ball_possession": parse_percent(stats_dict.get("Ball Possession")),
                "total_passes": parse_optional_int(stats_dict.get("Total passes")),
                "accurate_passes": parse_optional_int(stats_dict.get("Passes accurate")),
                "pass_percentage": parse_percent(stats_dict.get("Passes %")),
                "expected_goals": parse_percent(stats_dict.get("expected_goals")),
                "goal_kicks": parse_optional_int(stats_dict.get("Goal Kicks")),
                "throw_ins": parse_optional_int(stats_dict.get("Throw Ins")),
                "tackles": parse_optional_int(stats_dict.get("Tackles")),
                "raw_payload": raw_payload,
            }

        def first_half_looks_like_full_time(
            first_half_map: dict[int, dict[str, Any]],
            full_time_map: dict[int, dict[str, Any]],
        ) -> bool:
            comparable_fields = (
                "Shots on Goal",
                "Total Shots",
                "Corner Kicks",
                "Fouls",
                "Offsides",
                "Yellow Cards",
                "Red Cards",
                "Goalkeeper Saves",
            )
            compared = 0

            for team_id, first_half_snapshot in first_half_map.items():
                full_time_snapshot = full_time_map.get(team_id)
                if full_time_snapshot is None:
                    continue

                first_half_stats = first_half_snapshot["stats"]
                full_time_stats = full_time_snapshot["stats"]

                for field in comparable_fields:
                    first_half_value = parse_optional_int(first_half_stats.get(field))
                    full_time_value = parse_optional_int(full_time_stats.get(field))
                    if first_half_value is None or full_time_value is None:
                        continue
                    compared += 1
                    if first_half_value != full_time_value:
                        return False

            return compared > 0

        ft_snapshots = build_snapshot_map(full_time_statistics)
        if len(ft_snapshots) < 2:
            self.logger.warning(
                "Fixture %s recibio fixture_statistics incompletas. Se conserva el estado previo.",
                fixture_id,
            )
            return

        half_statistics_rows = first_half_statistics or []
        first_half_snapshots = build_snapshot_map(half_statistics_rows, "statistics_1h")
        second_half_snapshots = build_snapshot_map(half_statistics_rows, "statistics_2h")
        if not first_half_snapshots and half_statistics_rows:
            legacy_first_half_snapshots = build_snapshot_map(half_statistics_rows)
            if legacy_first_half_snapshots and not first_half_looks_like_full_time(
                legacy_first_half_snapshots,
                ft_snapshots,
            ):
                first_half_snapshots = legacy_first_half_snapshots

        if first_half_snapshots and set(first_half_snapshots.keys()) != set(ft_snapshots.keys()):
            self.logger.warning(
                "Fixture %s recibio fixture_statistics 1H parciales. Se omiten periodos 1H/2H hasta tener ambos equipos.",
                fixture_id,
            )
            first_half_snapshots = {}
            second_half_snapshots = {}
        if second_half_snapshots and set(second_half_snapshots.keys()) != set(ft_snapshots.keys()):
            self.logger.warning(
                "Fixture %s recibio fixture_statistics 2H parciales. Se omite 2H hasta tener ambos equipos.",
                fixture_id,
            )
            second_half_snapshots = {}

        rows: list[dict[str, Any]] = []

        for team_id, snapshot in ft_snapshots.items():
            ft_stats = snapshot["stats"]
            rows.append(build_period_row(team_id, "FT", ft_stats, snapshot["raw_payload"]))

            first_half_snapshot = first_half_snapshots.get(team_id)
            if first_half_snapshot is None:
                continue

            first_half_stats = first_half_snapshot["stats"]
            rows.append(build_period_row(team_id, "1H", first_half_stats, first_half_snapshot["raw_payload"]))

            second_half_snapshot = second_half_snapshots.get(team_id)
            if second_half_snapshot is not None:
                second_half_stats = second_half_snapshot["stats"]
                rows.append(build_period_row(team_id, "2H", second_half_stats, second_half_snapshot["raw_payload"]))
                continue

            second_half_row = {
                "fixture_id": fixture_id,
                "team_id": team_id,
                "period": "2H",
                "shots_on_target": subtract_optional_ints(ft_stats.get("Shots on Goal"), first_half_stats.get("Shots on Goal")),
                "shots_off_target": subtract_optional_ints(ft_stats.get("Shots off Goal"), first_half_stats.get("Shots off Goal")),
                "total_shots": subtract_optional_ints(ft_stats.get("Total Shots"), first_half_stats.get("Total Shots")),
                "blocked_shots": subtract_optional_ints(ft_stats.get("Blocked Shots"), first_half_stats.get("Blocked Shots")),
                "shots_inside_box": subtract_optional_ints(ft_stats.get("Shots insidebox"), first_half_stats.get("Shots insidebox")),
                "shots_outside_box": subtract_optional_ints(ft_stats.get("Shots outsidebox"), first_half_stats.get("Shots outsidebox")),
                "fouls": subtract_optional_ints(ft_stats.get("Fouls"), first_half_stats.get("Fouls")),
                "corners": subtract_optional_ints(ft_stats.get("Corner Kicks"), first_half_stats.get("Corner Kicks")),
                "offsides": subtract_optional_ints(ft_stats.get("Offsides"), first_half_stats.get("Offsides")),
                "yellow_cards": subtract_optional_ints(ft_stats.get("Yellow Cards"), first_half_stats.get("Yellow Cards")),
                "red_cards": subtract_optional_ints(ft_stats.get("Red Cards"), first_half_stats.get("Red Cards")),
                "booking_points": None,
                "goalkeeper_saves": subtract_optional_ints(ft_stats.get("Goalkeeper Saves"), first_half_stats.get("Goalkeeper Saves")),
                "ball_possession": None,
                "total_passes": subtract_optional_ints(ft_stats.get("Total passes"), first_half_stats.get("Total passes")),
                "accurate_passes": subtract_optional_ints(ft_stats.get("Passes accurate"), first_half_stats.get("Passes accurate")),
                "pass_percentage": None,
                "expected_goals": None,
                "goal_kicks": subtract_optional_ints(ft_stats.get("Goal Kicks"), first_half_stats.get("Goal Kicks")),
                "throw_ins": subtract_optional_ints(ft_stats.get("Throw Ins"), first_half_stats.get("Throw Ins")),
                "tackles": subtract_optional_ints(ft_stats.get("Tackles"), first_half_stats.get("Tackles")),
                "raw_payload": {
                    "source": "derived_ft_minus_1h",
                    "full_time": snapshot["raw_payload"],
                    "first_half": first_half_snapshot["raw_payload"],
                },
            }

            if any(
                second_half_row[field] is not None
                for field in (
                    "shots_on_target",
                    "shots_off_target",
                    "total_shots",
                    "blocked_shots",
                    "shots_inside_box",
                    "shots_outside_box",
                    "fouls",
                    "corners",
                    "offsides",
                    "yellow_cards",
                    "red_cards",
                    "goalkeeper_saves",
                    "total_passes",
                    "accurate_passes",
                    "goal_kicks",
                    "throw_ins",
                    "tackles",
                )
            ):
                rows.append(second_half_row)

        marker = utcnow().isoformat()
        stamped_rows = [{**row, "updated_at": marker} for row in rows]
        self._upsert_rows(
            "fixture_statistics",
            stamped_rows,
            "fixture_id,team_id,period",
            f"upsert fixture statistics fixture={fixture_id}",
        )
        self._delete_scope_rows(
            "fixture_statistics",
            {"fixture_id": fixture_id},
            f"cleanup fixture statistics fixture={fixture_id}",
            older_than=marker,
        )

    def replace_fixture_events(self, fixture_id: int, events: list[dict[str, Any]]) -> None:
        rows = []
        seen_keys: set[str] = set()
        team_points: dict[int, int] = {}

        for event in events:
            team = event.get("team") or {}
            player = event.get("player") or {}
            assist_player = event.get("assist") or {}
            time_payload = event.get("time") or {}
            team_id = team.get("id")
            detail = event.get("detail")
            comments = event.get("comments")
            event_type = normalize_fixture_event_type(event.get("type"), detail)
            raw_minute = time_payload.get("elapsed")
            raw_extra_time = time_payload.get("extra")
            minute = raw_minute if isinstance(raw_minute, int) and raw_minute >= 0 else None
            extra_time = raw_extra_time if isinstance(raw_extra_time, int) and raw_extra_time >= 0 else None
            player_id = normalize_optional_fk_id(player.get("id"))
            assist_player_id = normalize_optional_fk_id(assist_player.get("id"))

            if not team_id:
                continue
            if event_type == "Other" and detail is None and comments is None:
                continue

            hash_source = "|".join(
                str(part or "")
                for part in (
                    fixture_id,
                    team_id,
                    player_id,
                    assist_player_id,
                    minute,
                    extra_time,
                    event_type,
                    detail,
                    comments,
                )
            )
            event_hash = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()
            if event_hash in seen_keys:
                self.logger.warning(
                    "Evento duplicado omitido en fixture=%s team=%s minute=%s type=%s detail=%s",
                    fixture_id,
                    team_id,
                    raw_minute,
                    event_type,
                    detail,
                )
                continue

            seen_keys.add(event_hash)

            if player_id is not None:
                self._execute(
                    lambda player=player: self.supabase.table("players").upsert(
                        {
                            "id": player_id,
                            "name": player.get("name") or "Unknown",
                            "raw_payload": player,
                            "updated_at": utcnow().isoformat(),
                        }
                    ),
                    f"upsert event player fixture={fixture_id} player={player_id}",
                )

            if assist_player_id is not None:
                self._execute(
                    lambda assist_player=assist_player: self.supabase.table("players").upsert(
                        {
                            "id": assist_player_id,
                            "name": assist_player.get("name") or "Unknown",
                            "raw_payload": assist_player,
                            "updated_at": utcnow().isoformat(),
                        }
                    ),
                    f"upsert event assist fixture={fixture_id} player={assist_player_id}",
                )

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "team_id": team_id,
                    "player_id": player_id,
                    "assist_player_id": assist_player_id,
                    "elapsed": minute,
                    "extra_time": extra_time,
                    "type": event_type,
                    "detail": detail,
                    "comments": comments,
                    "event_hash": event_hash,
                    "raw_payload": event,
                }
            )

            if event_type == "Card" and isinstance(detail, str):
                if "Yellow" in detail:
                    team_points[team_id] = team_points.get(team_id, 0) + 10
                elif "Red" in detail:
                    team_points[team_id] = team_points.get(team_id, 0) + 25

        if not rows and not team_points:
            self.logger.warning(
                "Fixture %s recibio fixture_events sin filas utilizables. Se conserva el estado previo.",
                fixture_id,
            )
            return

        self._execute(
            lambda: self.supabase.table("fixture_events").delete().eq("fixture_id", fixture_id),
            f"delete fixture events fixture={fixture_id}",
        )
        self._execute(
            lambda: self.supabase.table("fixture_statistics")
            .update({"booking_points": None})
            .eq("fixture_id", fixture_id)
            .eq("period", "FT"),
            f"reset booking points fixture={fixture_id}",
        )

        if rows:
            self._execute(
                lambda: self.supabase.table("fixture_events").upsert(
                    rows,
                    on_conflict="fixture_id,event_hash",
                ),
                f"insert fixture events fixture={fixture_id}",
            )

        for team_id, points in team_points.items():
            self._execute(
                lambda team_id=team_id, points=points: self.supabase.table("fixture_statistics")
                .update({"booking_points": points})
                .eq("fixture_id", fixture_id)
                .eq("team_id", team_id)
                .eq("period", "FT"),
                f"update booking points fixture={fixture_id} team={team_id}",
            )

    def replace_card_events(self, fixture_id: int, events: list[dict[str, Any]]) -> None:
        self.replace_fixture_events(fixture_id, events)

    def count_player_stats_rows(self, fixture_id: int) -> int:
        response = self._execute(
            lambda: self.supabase.table("player_fixture_stats")
            .select("player_id")
            .eq("fixture_id", fixture_id),
            f"count player stats fixture={fixture_id}",
        )
        return len(response.data or [])

    def replace_player_stats(self, fixture_id: int, player_groups: list[dict[str, Any]]) -> int:
        player_catalog_rows: dict[int, dict[str, Any]] = {}
        rows = []
        for team_group in player_groups:
            team = team_group.get("team") or {}
            team_id = team.get("id")
            for player_item in team_group.get("players") or []:
                player = player_item.get("player") or {}
                stats = (player_item.get("statistics") or [{}])[0] or {}
                games = stats.get("games") or {}
                goals = stats.get("goals") or {}
                shots = stats.get("shots") or {}
                passes = stats.get("passes") or {}
                tackles = stats.get("tackles") or {}
                duels = stats.get("duels") or {}
                dribbles = stats.get("dribbles") or {}
                fouls = stats.get("fouls") or {}
                cards = stats.get("cards") or {}
                rating_value = games.get("rating")

                try:
                    rating = float(rating_value) if rating_value else None
                except (TypeError, ValueError):
                    rating = None

                if not player.get("id"):
                    continue

                player_catalog_rows[player["id"]] = {
                    "id": player.get("id"),
                    "name": player.get("name") or "Unknown",
                    "raw_payload": player,
                    "updated_at": utcnow().isoformat(),
                }

                rows.append(
                    {
                        "fixture_id": fixture_id,
                        "team_id": team_id,
                        "player_id": player["id"],
                        "position": games.get("position"),
                        "minutes": parse_int(games.get("minutes")),
                        "number": games.get("number"),
                        "captain": games.get("captain"),
                        "substitute": games.get("substitute"),
                        "rating": rating,
                        "goals": parse_int(goals.get("total")),
                        "assists": parse_int(goals.get("assists")),
                        "conceded": parse_int(goals.get("conceded")),
                        "saves": parse_int(goals.get("saves")),
                        "total_shots": parse_int(shots.get("total")),
                        "shots_on_target": parse_int(shots.get("on")),
                        "passes": parse_int(passes.get("total")),
                        "key_passes": parse_int(passes.get("key")),
                        "pass_accuracy": normalize_player_pass_accuracy(passes.get("total"), passes.get("accuracy")),
                        "tackles": parse_int(tackles.get("total")),
                        "blocks": parse_int(tackles.get("blocks")),
                        "interceptions": parse_int(tackles.get("interceptions")),
                        "duels_total": parse_int(duels.get("total")),
                        "duels_won": parse_int(duels.get("won")),
                        "dribble_attempts": parse_int(dribbles.get("attempts")),
                        "dribble_success": parse_int(dribbles.get("success")),
                        "dribble_past": parse_int(dribbles.get("past")),
                        "fouls_committed": parse_int(fouls.get("committed")),
                        "fouls_drawn": parse_int(fouls.get("drawn")),
                        "offsides": parse_int(stats.get("offsides")),
                        "yellow_cards": parse_int(cards.get("yellow")),
                        "red_cards": parse_int(cards.get("red")),
                        "raw_payload": player_item,
                        "updated_at": utcnow().isoformat(),
                    }
                )

        for batch in chunked(list(player_catalog_rows.values()), 500):
            self._execute(
                lambda batch=batch: self.supabase.table("players").upsert(list(batch)),
                f"upsert players fixture={fixture_id} batch={len(batch)}",
            )

        if rows:
            marker = utcnow().isoformat()
            stamped_rows = [{**row, "updated_at": marker} for row in rows]
            self._upsert_rows(
                "player_fixture_stats",
                stamped_rows,
                "fixture_id,player_id",
                f"upsert player stats fixture={fixture_id}",
            )
            self._delete_scope_rows(
                "player_fixture_stats",
                {"fixture_id": fixture_id},
                f"cleanup player stats fixture={fixture_id}",
                older_than=marker,
            )
        else:
            self.logger.warning(
                "Fixture %s recibio fixtures/players sin filas utilizables. Se conserva el estado previo.",
                fixture_id,
            )

        persisted_rows = self.count_player_stats_rows(fixture_id)
        if rows and persisted_rows == 0:
            self.logger.error(
                "Fixture %s recibio %s filas de player stats pero no persistio ninguna en player_fixture_stats.",
                fixture_id,
                len(rows),
            )
        return persisted_rows

    def replace_team_fixture_facts(self, fixture_id: int) -> bool:
        fixture = self.get_fixture_context_row(fixture_id)
        if not fixture or not is_final_status(fixture.get("status_short")):
            return False

        stats_response = self._execute(
            lambda: self.supabase.table("fixture_statistics")
            .select("*")
            .eq("fixture_id", fixture_id),
            f"load fixture statistics for facts fixture={fixture_id}",
        )
        stats_rows = stats_response.data or []
        ft_rows = [row for row in stats_rows if row.get("period") == "FT"]
        if len(ft_rows) < 2:
            self.logger.warning("Fixture %s sin estadisticas completas; no se generan team_fixture_facts.", fixture_id)
            return False

        stats_by_period_team = {
            (str(row.get("period") or "FT"), row.get("team_id")): row
            for row in stats_rows
            if row.get("team_id") is not None
        }
        home_team_id = fixture.get("home_team_id")
        away_team_id = fixture.get("away_team_id")
        home_stats = stats_by_period_team.get(("FT", home_team_id)) or {}
        away_stats = stats_by_period_team.get(("FT", away_team_id)) or {}
        home_stats_1h = stats_by_period_team.get(("1H", home_team_id)) or {}
        away_stats_1h = stats_by_period_team.get(("1H", away_team_id)) or {}
        home_stats_2h = stats_by_period_team.get(("2H", home_team_id)) or {}
        away_stats_2h = stats_by_period_team.get(("2H", away_team_id)) or {}
        if not home_team_id or not away_team_id or not home_stats or not away_stats:
            self.logger.warning("Fixture %s no tiene estadisticas para ambos equipos.", fixture_id)
            return False

        def second_half(total: Any, first_half: Any) -> int | None:
            return subtract_optional_ints(total, first_half)

        def derive_booking_points(row: dict[str, Any]) -> int | None:
            explicit_value = parse_optional_int(row.get("booking_points"))
            if explicit_value is not None:
                return explicit_value

            yellow_cards = parse_optional_int(row.get("yellow_cards"))
            red_cards = parse_optional_int(row.get("red_cards"))
            if yellow_cards is None or red_cards is None:
                return None
            return (yellow_cards * 10) + (red_cards * 25)

        home_goals = fixture.get("home_goals")
        away_goals = fixture.get("away_goals")
        home_goals_1h = fixture.get("home_goals_1h")
        away_goals_1h = fixture.get("away_goals_1h")
        home_goals_2h = second_half(home_goals, home_goals_1h)
        away_goals_2h = second_half(away_goals, away_goals_1h)

        def result_for(goals_for: Any, goals_against: Any) -> str | None:
            if goals_for is None or goals_against is None:
                return None
            if parse_int(goals_for) > parse_int(goals_against):
                return "win"
            if parse_int(goals_for) < parse_int(goals_against):
                return "loss"
            return "draw"

        def build_row(team_id: int, opponent_id: int, own: dict, opp: dict, is_home: bool) -> dict[str, Any]:
            goals_for = home_goals if is_home else away_goals
            goals_against = away_goals if is_home else home_goals
            goals_for_1h = home_goals_1h if is_home else away_goals_1h
            goals_against_1h = away_goals_1h if is_home else home_goals_1h
            goals_for_2h = home_goals_2h if is_home else away_goals_2h
            goals_against_2h = away_goals_2h if is_home else home_goals_2h

            yellow_for = parse_optional_int(own.get("yellow_cards"))
            red_for = parse_optional_int(own.get("red_cards"))
            yellow_against = parse_optional_int(opp.get("yellow_cards"))
            red_against = parse_optional_int(opp.get("red_cards"))
            cards_for = sum_optional_ints(yellow_for, red_for)
            cards_against = sum_optional_ints(yellow_against, red_against)
            booking_points_for = derive_booking_points(own)
            booking_points_against = derive_booking_points(opp)
            own_1h = home_stats_1h if is_home else away_stats_1h
            opp_1h = away_stats_1h if is_home else home_stats_1h
            own_2h = home_stats_2h if is_home else away_stats_2h
            opp_2h = away_stats_2h if is_home else home_stats_2h
            corners_for_2h = own_2h.get("corners")
            if corners_for_2h is None:
                corners_for_2h = second_half(own.get("corners"), own_1h.get("corners"))
            corners_against_2h = opp_2h.get("corners")
            if corners_against_2h is None:
                corners_against_2h = second_half(opp.get("corners"), opp_1h.get("corners"))

            return {
                "fixture_id": fixture_id,
                "team_id": team_id,
                "opponent_team_id": opponent_id,
                "league_id": fixture.get("league_id"),
                "season": fixture.get("season"),
                "played_at": fixture.get("date"),
                "is_home": is_home,
                "venue_scope": "home" if is_home else "away",
                "result": result_for(goals_for, goals_against),
                "goals_for": goals_for,
                "goals_against": goals_against,
                "total_match_goals": parse_int(goals_for) + parse_int(goals_against) if goals_for is not None and goals_against is not None else None,
                "goals_for_1h": goals_for_1h,
                "goals_against_1h": goals_against_1h,
                "total_1h_goals": parse_int(goals_for_1h) + parse_int(goals_against_1h) if goals_for_1h is not None and goals_against_1h is not None else None,
                "goals_for_2h": goals_for_2h,
                "goals_against_2h": goals_against_2h,
                "total_2h_goals": parse_int(goals_for_2h) + parse_int(goals_against_2h) if goals_for_2h is not None and goals_against_2h is not None else None,
                "corners_for": own.get("corners"),
                "corners_against": opp.get("corners"),
                "total_corners": sum_optional_ints(own.get("corners"), opp.get("corners")),
                "corners_for_1h": own_1h.get("corners"),
                "corners_against_1h": opp_1h.get("corners"),
                "total_corners_1h": sum_optional_ints(own_1h.get("corners"), opp_1h.get("corners")),
                "corners_for_2h": corners_for_2h,
                "corners_against_2h": corners_against_2h,
                "total_corners_2h": sum_optional_ints(corners_for_2h, corners_against_2h),
                "yellow_cards_for": yellow_for,
                "red_cards_for": red_for,
                "cards_for": cards_for,
                "yellow_cards_against": yellow_against,
                "red_cards_against": red_against,
                "cards_against": cards_against,
                "total_cards": sum_optional_ints(cards_for, cards_against),
                "booking_points_for": booking_points_for,
                "booking_points_against": booking_points_against,
                "total_booking_points": sum_optional_ints(booking_points_for, booking_points_against),
                "fouls_committed": own.get("fouls"),
                "fouls_won": opp.get("fouls"),
                "total_fouls": sum_optional_ints(own.get("fouls"), opp.get("fouls")),
                "offsides_for": own.get("offsides"),
                "offsides_against": opp.get("offsides"),
                "total_offsides": sum_optional_ints(own.get("offsides"), opp.get("offsides")),
                "total_shots_for": own.get("total_shots"),
                "total_shots_against": opp.get("total_shots"),
                "shots_on_target_for": own.get("shots_on_target"),
                "shots_on_target_against": opp.get("shots_on_target"),
                "goal_kicks_for": own.get("goal_kicks"),
                "goal_kicks_against": opp.get("goal_kicks"),
                "total_goal_kicks": sum_optional_ints(own.get("goal_kicks"), opp.get("goal_kicks")),
                "throw_ins_for": own.get("throw_ins"),
                "throw_ins_against": opp.get("throw_ins"),
                "total_throw_ins": sum_optional_ints(own.get("throw_ins"), opp.get("throw_ins")),
                "tackles_for": own.get("tackles"),
                "tackles_against": opp.get("tackles"),
                "total_tackles": sum_optional_ints(own.get("tackles"), opp.get("tackles")),
                "data_quality": "ok",
                "updated_at": utcnow().isoformat(),
            }

        rows = [
            build_row(home_team_id, away_team_id, home_stats, away_stats, True),
            build_row(away_team_id, home_team_id, away_stats, home_stats, False),
        ]
        self._execute(
            lambda: self.supabase.table("team_fixture_facts").upsert(rows, on_conflict="fixture_id,team_id"),
            f"upsert team fixture facts fixture={fixture_id}",
        )
        return True

    def replace_referee_fixture_fact(self, fixture_id: int) -> dict[str, Any] | None:
        fixture = self.get_fixture_context_row(fixture_id)
        if not fixture or not is_final_status(fixture.get("status_short")):
            return None

        referee_id = fixture.get("referee_id")
        if referee_id is None and fixture.get("referee_name_raw"):
            referee_id = self.upsert_referee_from_name(fixture.get("referee_name_raw"))
            if referee_id is not None:
                self._execute(
                    lambda: self.supabase.table("fixtures")
                    .update({"referee_id": referee_id, "updated_at": utcnow().isoformat()})
                    .eq("id", fixture_id),
                    f"attach referee fixture={fixture_id} referee={referee_id}",
                )

        if referee_id is None:
            return None

        stats_response = self._execute(
            lambda: self.supabase.table("fixture_statistics")
            .select("*")
            .eq("fixture_id", fixture_id)
            .eq("period", "FT"),
            f"load fixture statistics for referee facts fixture={fixture_id}",
        )
        stats_rows = stats_response.data or []
        if len(stats_rows) < 2:
            return None

        stats_by_team = {row.get("team_id"): row for row in stats_rows if row.get("team_id") is not None}
        home_team_id = fixture.get("home_team_id")
        away_team_id = fixture.get("away_team_id")
        home_stats = stats_by_team.get(home_team_id) or {}
        away_stats = stats_by_team.get(away_team_id) or {}
        if not home_team_id or not away_team_id or not home_stats or not away_stats:
            return None

        def card_count(row: dict[str, Any]) -> int | None:
            yellow = parse_optional_int(row.get("yellow_cards"))
            red = parse_optional_int(row.get("red_cards"))
            return sum_optional_ints(yellow, red)

        def booking_points(row: dict[str, Any]) -> int | None:
            explicit_value = parse_optional_int(row.get("booking_points"))
            if explicit_value is not None:
                return explicit_value

            yellow = parse_optional_int(row.get("yellow_cards"))
            red = parse_optional_int(row.get("red_cards"))
            if yellow is None or red is None:
                return None
            return (yellow * 10) + (red * 25)

        home_cards = card_count(home_stats)
        away_cards = card_count(away_stats)
        home_yellows = parse_optional_int(home_stats.get("yellow_cards"))
        away_yellows = parse_optional_int(away_stats.get("yellow_cards"))
        home_reds = parse_optional_int(home_stats.get("red_cards"))
        away_reds = parse_optional_int(away_stats.get("red_cards"))
        home_booking_points = booking_points(home_stats)
        away_booking_points = booking_points(away_stats)
        total_fouls = sum_optional_ints(home_stats.get("fouls"), away_stats.get("fouls"))

        event_response = self._execute(
            lambda: self.supabase.table("fixture_events")
            .select("type,detail,comments")
            .eq("fixture_id", fixture_id),
            f"load fixture events for referee facts fixture={fixture_id}",
        )
        penalties = 0
        for event in event_response.data or []:
            event_text = " ".join(
                str(part or "")
                for part in (event.get("type"), event.get("detail"), event.get("comments"))
            ).lower()
            if "penalty" in event_text and "shootout" not in event_text:
                penalties += 1

        row = {
            "fixture_id": fixture_id,
            "referee_id": referee_id,
            "league_id": fixture.get("league_id"),
            "season": fixture.get("season"),
            "played_at": fixture.get("date"),
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_cards": home_cards,
            "away_cards": away_cards,
            "total_cards": sum_optional_ints(home_cards, away_cards),
            "home_booking_points": home_booking_points,
            "away_booking_points": away_booking_points,
            "total_booking_points": sum_optional_ints(home_booking_points, away_booking_points),
            "total_yellow_cards": sum_optional_ints(home_yellows, away_yellows),
            "total_red_cards": sum_optional_ints(home_reds, away_reds),
            "total_fouls": total_fouls,
            "penalties": penalties,
            "updated_at": utcnow().isoformat(),
        }
        self._execute(
            lambda: self.supabase.table("referee_fixture_facts").upsert(row, on_conflict="fixture_id"),
            f"upsert referee fixture fact fixture={fixture_id}",
        )
        return row

    def upsert_prediction(self, fixture_id: int, prediction_bundle: dict[str, Any]) -> None:
        predictions = prediction_bundle.get("predictions") or {}
        winner = predictions.get("winner") or {}
        percent = predictions.get("percent") or {}

        self._execute(
            lambda: self.supabase.table("fixture_predictions_api").upsert(
                {
                    "fixture_id": fixture_id,
                    "winner_team_id": winner.get("id"),
                    "winner_name": winner.get("name"),
                    "winner_comment": winner.get("comment"),
                    "advice": predictions.get("advice"),
                    "percent_home": parse_percent(percent.get("home")),
                    "percent_draw": parse_percent(percent.get("draw")),
                    "percent_away": parse_percent(percent.get("away")),
                    "raw_payload": prediction_bundle,
                    "updated_at": utcnow().isoformat(),
                }
            ),
            f"upsert prediction fixture={fixture_id}",
        )

    def upsert_lineups(self, fixture_id: int, lineups: list[dict[str, Any]]) -> None:
        rows = []
        for lineup in lineups:
            team = lineup.get("team") or {}
            coach = lineup.get("coach") or {}
            team_id = team.get("id")
            coach_id = normalize_optional_fk_id(coach.get("id"))
            if not team_id:
                continue

            if coach_id is not None:
                self._execute(
                    lambda coach=coach: self.supabase.table("coaches").upsert(
                        {
                            "id": coach_id,
                            "name": coach.get("name") or "Unknown",
                            "photo_url": coach.get("photo"),
                            "raw_payload": coach,
                            "updated_at": utcnow().isoformat(),
                        }
                    ),
                    f"upsert lineup coach fixture={fixture_id} coach={coach_id}",
                )

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "team_id": team_id,
                    "formation": lineup.get("formation"),
                    "coach_id": coach_id,
                    "start_xi": lineup.get("startXI") or [],
                    "substitutes": lineup.get("substitutes") or [],
                    "raw_payload": lineup,
                    "updated_at": utcnow().isoformat(),
                }
            )

        if rows:
            self._execute(
                lambda: self.supabase.table("fixture_lineups").upsert(rows, on_conflict="fixture_id,team_id"),
                f"upsert lineups fixture={fixture_id}",
            )

    def store_odds_snapshots(
        self,
        market_scope: str,
        captured_at: datetime,
        bookmaker_id: int,
        bookmaker_name: str,
        odds_items: list[dict[str, Any]],
    ) -> None:
        rows = []
        for item in odds_items:
            fixture = item.get("fixture") or {}
            fixture_id = fixture.get("id")
            if not fixture_id:
                continue

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "bookmaker_id": bookmaker_id,
                    "bookmaker_name": bookmaker_name,
                    "market_scope": market_scope,
                    "captured_at": captured_at.isoformat(),
                    "payload": item,
                }
            )

        if rows:
            self._execute(
                lambda: self.supabase.table("fixture_odds_snapshots").insert(rows),
                f"insert odds snapshots count={len(rows)}",
            )

    def get_candidate_fixtures(self, start_at: datetime, end_at: datetime, statuses: Sequence[str]) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("fixtures")
            .select("id,date,league_id,season,status_short")
            .gte("date", start_at.isoformat())
            .lte("date", end_at.isoformat())
            .in_("status_short", list(statuses))
            .order("date", desc=False),
            f"load candidate fixtures statuses={','.join(statuses)}",
        )
        return response.data or []

    def get_fixture_context_row(self, fixture_id: int) -> dict[str, Any] | None:
        response = self._execute(
            lambda: self.supabase.table("fixtures")
            .select(
                "id,date,league_id,season,status_short,referee_id,referee_name_raw,"
                "home_team_id,away_team_id,home_goals,away_goals,home_goals_1h,away_goals_1h"
            )
            .eq("id", fixture_id)
            .limit(1),
            f"load fixture context fixture={fixture_id}",
        )
        rows = response.data or []
        return rows[0] if rows else None

    def canonicalize_referees_for_league_season(self, league_id: int, season: int) -> None:
        response = self._execute(
            lambda: self.supabase.table("fixtures")
            .select("id,referee_id,referee_name_raw,raw_payload")
            .eq("league_id", league_id)
            .eq("season", season),
            f"load fixtures for referee canonicalization league={league_id} season={season}",
        )

        for row in response.data or []:
            referee_name = row.get("referee_name_raw")
            if not referee_name:
                continue

            referee_id = self.upsert_referee_from_name(
                referee_name,
                raw_payload=row.get("raw_payload") or {},
            )
            if referee_id is None or row.get("referee_id") == referee_id:
                continue

            self._execute(
                lambda fixture_id=row["id"], referee_id=referee_id: self.supabase.table("fixtures")
                .update({"referee_id": referee_id, "updated_at": utcnow().isoformat()})
                .eq("id", fixture_id),
                f"attach referee fixture={row['id']} referee={referee_id}",
            )

    def get_referee_rebuild_fixture_ids(self, league_id: int, season: int) -> list[int]:
        response = self._execute(
            lambda: self.supabase.table("fixtures")
            .select("id,status_short,referee_id")
            .eq("league_id", league_id)
            .eq("season", season)
            .order("date", desc=False),
            f"load referee rebuild fixtures league={league_id} season={season}",
        )
        return [
            int(row["id"])
            for row in response.data or []
            if row.get("id") is not None and row.get("referee_id") is not None and is_final_status(row.get("status_short"))
        ]

    def get_referee_rebuild_fixture_rows(self, league_id: int, season: int) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("fixtures")
            .select(
                "id,date,league_id,season,status_short,referee_id,referee_name_raw,home_team_id,away_team_id"
            )
            .eq("league_id", league_id)
            .eq("season", season)
            .order("date", desc=False),
            f"load referee rebuild fixture rows league={league_id} season={season}",
        )
        return [
            row
            for row in response.data or []
            if row.get("id") is not None and row.get("referee_id") is not None and is_final_status(row.get("status_short"))
        ]

    def get_fixture_statistics_rows_for_fixtures(self, fixture_ids: Sequence[int], period: str = "FT") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for batch in chunked(list(fixture_ids), 200):
            response = self._execute(
                lambda batch=batch: self.supabase.table("fixture_statistics")
                .select(
                    "fixture_id,team_id,period,yellow_cards,red_cards,booking_points,fouls,cards"
                )
                .in_("fixture_id", list(batch))
                .eq("period", period),
                f"load fixture statistics batch={len(batch)} period={period}",
            )
            rows.extend(response.data or [])
        return rows

    def get_fixture_event_rows_for_fixtures(self, fixture_ids: Sequence[int]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for batch in chunked(list(fixture_ids), 200):
            response = self._execute(
                lambda batch=batch: self.supabase.table("fixture_events")
                .select("fixture_id,type,detail,comments")
                .in_("fixture_id", list(batch)),
                f"load fixture events batch={len(batch)}",
            )
            rows.extend(response.data or [])
        return rows

    def replace_referee_fixture_facts_for_league(self, league_id: int, season: int, rows: list[dict[str, Any]]) -> None:
        filters = {"league_id": league_id, "season": season}
        if not rows:
            self._delete_scope_rows(
                "referee_fixture_facts",
                filters,
                f"delete referee fixture facts league={league_id} season={season}",
            )
            return

        marker = utcnow().isoformat()
        stamped_rows = [{**row, "updated_at": marker} for row in rows]
        self._upsert_rows(
            "referee_fixture_facts",
            stamped_rows,
            "fixture_id",
            f"upsert referee fixture facts league={league_id} season={season}",
        )
        self._delete_scope_rows(
            "referee_fixture_facts",
            filters,
            f"cleanup referee fixture facts league={league_id} season={season}",
            older_than=marker,
        )

    def get_referee_ids_for_league_season(self, league_id: int, season: int) -> list[int]:
        response = self._execute(
            lambda: self.supabase.table("referee_fixture_facts")
            .select("referee_id")
            .eq("league_id", league_id)
            .eq("season", season),
            f"load referee ids league={league_id} season={season}",
        )
        return sorted({int(row["referee_id"]) for row in response.data or [] if row.get("referee_id") is not None})

    def get_referee_fixture_fact_rows(self, referee_id: int, league_id: int, season: int) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("referee_fixture_facts")
            .select(
                """
                fixture_id,
                referee_id,
                league_id,
                season,
                played_at,
                home_team_id,
                away_team_id,
                home_cards,
                away_cards,
                total_cards,
                home_booking_points,
                away_booking_points,
                total_booking_points,
                total_yellow_cards,
                total_red_cards,
                total_fouls,
                penalties
                """
            )
            .eq("referee_id", referee_id)
            .eq("league_id", league_id)
            .eq("season", season)
            .order("played_at", desc=True),
            f"load referee fixture facts referee={referee_id} league={league_id} season={season}",
        )
        return response.data or []

    def get_team_ids_for_league_season(self, league_id: int, season: int) -> list[int]:
        response = self._execute(
            lambda: self.supabase.table("team_fixture_facts")
            .select("team_id")
            .eq("league_id", league_id)
            .eq("season", season),
            f"load trend team ids league={league_id} season={season}",
        )
        return sorted({row["team_id"] for row in response.data or [] if row.get("team_id")})

    def get_team_fixture_fact_rows(self, team_id: int, league_id: int, season: int) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("team_fixture_facts")
            .select(
                """
                fixture_id,
                team_id,
                opponent_team_id,
                league_id,
                season,
                played_at,
                venue_scope,
                result,
                goals_for,
                goals_against,
                total_match_goals,
                goals_for_1h,
                goals_against_1h,
                total_1h_goals,
                goals_for_2h,
                goals_against_2h,
                total_2h_goals,
                corners_for,
                corners_against,
                total_corners,
                corners_for_1h,
                corners_against_1h,
                total_corners_1h,
                corners_for_2h,
                corners_against_2h,
                total_corners_2h,
                cards_for,
                cards_against,
                total_cards,
                booking_points_for,
                booking_points_against,
                total_booking_points,
                fouls_committed,
                fouls_won,
                total_fouls,
                offsides_for,
                offsides_against,
                total_offsides,
                total_shots_for,
                total_shots_against,
                shots_on_target_for,
                shots_on_target_against,
                goal_kicks_for,
                goal_kicks_against,
                total_goal_kicks,
                throw_ins_for,
                throw_ins_against,
                total_throw_ins,
                tackles_for,
                tackles_against,
                total_tackles
                """
            )
            .eq("team_id", team_id)
            .eq("league_id", league_id)
            .eq("season", season)
            .order("played_at", desc=True),
            f"load team fixture facts team={team_id} league={league_id} season={season}",
        )
        return response.data or []

    def get_player_fixture_stat_rows(self, team_id: int, league_id: int, season: int) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("player_fixture_stats")
            .select(
                """
                fixture_id,
                player_id,
                team_id,
                league_id,
                season,
                is_home,
                minutes,
                substitute,
                goals,
                assists,
                total_shots,
                shots_on_target,
                yellow_cards,
                red_cards,
                fouls_committed,
                fouls_drawn,
                tackles,
                offsides
                """
            )
            .eq("team_id", team_id)
            .eq("league_id", league_id)
            .eq("season", season),
            f"load player fixture stats team={team_id} league={league_id} season={season}",
        )
        return response.data or []

    def replace_player_season_stats(self, team_id: int, league_id: int, season: int, rows: list[dict[str, Any]]) -> None:
        filters = {"team_id": team_id, "league_id": league_id, "season": season}
        if not rows:
            self._delete_scope_rows(
                "player_season_stats",
                filters,
                f"delete player season stats team={team_id} league={league_id} season={season}",
            )
            return

        marker = utcnow().isoformat()
        stamped_rows = [{**row, "updated_at": marker} for row in rows]
        self._upsert_rows(
            "player_season_stats",
            stamped_rows,
            "player_id,team_id,league_id,season,scope",
            f"upsert player season stats team={team_id} league={league_id} season={season}",
        )
        self._delete_scope_rows(
            "player_season_stats",
            filters,
            f"cleanup player season stats team={team_id} league={league_id} season={season}",
            older_than=marker,
        )

    def replace_team_market_results(self, team_id: int, league_id: int, season: int, rows: list[dict[str, Any]]) -> None:
        deduped_rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[Any, Any, Any, Any]] = set()
        for row in rows:
            dedupe_key = (
                row.get("fixture_id"),
                row.get("team_id"),
                row.get("scope"),
                row.get("market_key"),
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            deduped_rows.append(row)

        if len(deduped_rows) != len(rows):
            self.logger.warning(
                "Se deduplicaron %s rows de team_match_market_results para team=%s league=%s season=%s",
                len(rows) - len(deduped_rows),
                team_id,
                league_id,
                season,
            )

        filters = {"team_id": team_id, "league_id": league_id, "season": season}
        if not deduped_rows:
            self._delete_scope_rows(
                "team_match_market_results",
                filters,
                f"delete team market results team={team_id} league={league_id} season={season}",
            )
            return

        existing_response = self._execute(
            lambda: self.supabase.table("team_match_market_results")
            .select("fixture_id,team_id,scope,market_key")
            .eq("team_id", team_id)
            .eq("league_id", league_id)
            .eq("season", season),
            f"load existing team market results team={team_id} league={league_id} season={season}",
        )
        existing_keys = {
            (
                row.get("fixture_id"),
                row.get("team_id"),
                row.get("scope"),
                row.get("market_key"),
            )
            for row in existing_response.data or []
        }
        next_keys = {
            (
                row.get("fixture_id"),
                row.get("team_id"),
                row.get("scope"),
                row.get("market_key"),
            )
            for row in deduped_rows
        }

        self._upsert_rows(
            "team_match_market_results",
            deduped_rows,
            "fixture_id,team_id,scope,market_key",
            f"upsert team market results team={team_id} league={league_id} season={season}",
        )

        stale_keys = existing_keys - next_keys
        for fixture_id, stale_team_id, scope, market_key in stale_keys:
            self._execute(
                lambda fixture_id=fixture_id, stale_team_id=stale_team_id, scope=scope, market_key=market_key: self.supabase.table("team_match_market_results")
                .delete()
                .eq("fixture_id", fixture_id)
                .eq("team_id", stale_team_id)
                .eq("scope", scope)
                .eq("market_key", market_key),
                f"delete stale team market result fixture={fixture_id} team={stale_team_id} scope={scope} market={market_key}",
            )

    def replace_team_season_market_stats(self, team_id: int, league_id: int, season: int, rows: list[dict[str, Any]]) -> None:
        filters = {"team_id": team_id, "league_id": league_id, "season": season}
        if not rows:
            self._delete_scope_rows(
                "team_season_market_stats",
                filters,
                f"delete team season market stats team={team_id} league={league_id} season={season}",
            )
            return

        marker = utcnow().isoformat()
        stamped_rows = [{**row, "updated_at": marker} for row in rows]
        self._upsert_rows(
            "team_season_market_stats",
            stamped_rows,
            "team_id,league_id,season,scope,market_key",
            f"upsert team season market stats team={team_id} league={league_id} season={season}",
        )
        self._delete_scope_rows(
            "team_season_market_stats",
            filters,
            f"cleanup team season market stats team={team_id} league={league_id} season={season}",
            older_than=marker,
        )

    def replace_referee_market_stats(self, referee_id: int, league_id: int, season: int, rows: list[dict[str, Any]]) -> None:
        filters = {"referee_id": referee_id, "league_id": league_id, "season": season}
        if not rows:
            self._delete_scope_rows(
                "referee_market_stats",
                filters,
                f"delete referee market stats referee={referee_id} league={league_id} season={season}",
            )
            return

        marker = utcnow().isoformat()
        stamped_rows = [{**row, "updated_at": marker} for row in rows]
        self._upsert_rows(
            "referee_market_stats",
            stamped_rows,
            "referee_id,league_id,season,market_key",
            f"upsert referee market stats referee={referee_id} league={league_id} season={season}",
        )
        self._delete_scope_rows(
            "referee_market_stats",
            filters,
            f"cleanup referee market stats referee={referee_id} league={league_id} season={season}",
            older_than=marker,
        )

    def replace_team_stat_averages(self, team_id: int, league_id: int, season: int, rows: list[dict[str, Any]]) -> None:
        filters = {"team_id": team_id, "league_id": league_id, "season": season}
        if not rows:
            self._delete_scope_rows(
                "team_stat_averages",
                filters,
                f"delete team stat averages team={team_id} league={league_id} season={season}",
            )
            return

        marker = utcnow().isoformat()
        stamped_rows = [{**row, "updated_at": marker} for row in rows]
        self._upsert_rows(
            "team_stat_averages",
            stamped_rows,
            "team_id,league_id,season,scope",
            f"upsert team stat averages team={team_id} league={league_id} season={season}",
        )
        self._delete_scope_rows(
            "team_stat_averages",
            filters,
            f"cleanup team stat averages team={team_id} league={league_id} season={season}",
            older_than=marker,
        )


async def sync_reference_catalogs(
    api_client: ApiFootballClient,
    repository: StufRepository,
    settings: Settings,
    target_leagues: Sequence[int] | None = None,
    include_odds_catalogs: bool = True,
) -> dict[tuple[int, int], dict[str, Any]]:
    scoped_target_leagues = tuple(target_leagues or settings.target_leagues)
    if not scoped_target_leagues:
        scoped_target_leagues = repository.get_supported_league_ids(feature="pipeline")
    if not scoped_target_leagues:
        raise RuntimeError("No supported leagues configured. Add rows to supported_leagues or pass --leagues.")

    for league_id in scoped_target_leagues:
        payload = await api_client.fetch("leagues", {"id": league_id})
        for item in (payload or {}).get("response", []):
            repository.upsert_league_catalog_entry(item)

    if include_odds_catalogs:
        bookmakers = await api_client.fetch("odds/bookmakers")
        repository.sync_bookmakers((bookmakers or {}).get("response", []))

        prematch_bets = await api_client.fetch("odds/bets")
        repository.sync_bets("prematch", (prematch_bets or {}).get("response", []))

        live_bets = await api_client.fetch("odds/live/bets")
        repository.sync_bets("live", (live_bets or {}).get("response", []))

    return repository.load_coverage_map()


def league_supports(
    coverage_map: dict[tuple[int, int], dict[str, Any]],
    league_id: int | None,
    season: int | None,
    field_name: str,
) -> bool:
    if league_id is None or season is None:
        return False

    row = coverage_map.get((league_id, season))
    return bool(row and row.get(field_name))


def is_final_status(status: str | None) -> bool:
    return bool(status in FINAL_STATUSES)


def should_skip_finished_fanout(
    repository: StufRepository,
    fixture_id: int,
    status: str | None,
    require_events: bool = True,
    require_players: bool = True,
    require_prediction: bool = True,
) -> bool:
    if not is_final_status(status):
        return False

    fixture_rows = repository.get_fixture_rows([fixture_id])
    existing = fixture_rows.get(fixture_id)
    if not existing or not is_final_status(existing.get("status_short")):
        return False

    health = repository.get_fixture_detail_health(fixture_id)
    return (
        health["has_stats"]
        and (not require_events or health["has_events"])
        and (not require_players or health["has_players"])
        and (not require_prediction or health["has_prediction"])
    )


def prediction_needs_refresh(repository: StufRepository, fixture_id: int, fixture_date: str | None) -> bool:
    row = repository.get_prediction_row(fixture_id)
    if row is None:
        return True

    updated_at = parse_iso_datetime(row.get("updated_at"))
    fixture_at = parse_iso_datetime(fixture_date)
    if updated_at is None or fixture_at is None:
        return True

    hours_until_fixture = (fixture_at - utcnow()).total_seconds() / 3600
    if hours_until_fixture <= 12:
        return updated_at < utcnow() - timedelta(hours=2)

    return False


def parse_cli_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--date", dest="target_date", help="Fecha YYYY-MM-DD para jobs diarios.")
    parser.add_argument("--season", type=int, help="Temporada YYYY para ingesta historica.")
    parser.add_argument("--limit", type=int, help="Limite por liga para ingesta historica segura.")
    parser.add_argument("--days", type=int, default=5, help="Dias futuros a planificar.")
    parser.add_argument("--window-hours", type=int, default=3, help="Ventana horaria para odds.")
    parser.add_argument("--leagues", help="Lista CSV de league_id para esta corrida, ej: 140 o 39,140.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football. Usar 1.0 para bootstrap seguro.",
    )
    parser.add_argument("--skip-players", action="store_true", help="No llamar /fixtures/players en esta corrida.")
    parser.add_argument("--skip-predictions", action="store_true", help="No llamar /predictions en esta corrida.")
    return parser.parse_args()


def resolve_target_leagues(
    args: argparse.Namespace,
    settings: Settings,
    repository: StufRepository | None = None,
    *,
    feature: str = "pipeline",
    season: int | None = None,
) -> tuple[int, ...]:
    raw_leagues = getattr(args, "leagues", None)
    if raw_leagues:
        return parse_target_leagues(raw_leagues)

    if settings.target_leagues:
        return settings.target_leagues

    if repository is None:
        raise RuntimeError("No supported leagues configured. Add rows to supported_leagues or pass --leagues.")

    resolved_season = season if season is not None else getattr(args, "season", None)
    configured_leagues = repository.get_supported_league_ids(feature=feature, season=resolved_season)
    if configured_leagues:
        return configured_leagues

    raise RuntimeError("No supported leagues configured. Add rows to supported_leagues or pass --leagues.")
