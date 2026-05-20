import asyncio
import os

from dotenv import load_dotenv

from pipeline_core import ApiFootballClient, Settings, configure_logging, parse_cli_args

LOGGER = configure_logging("stuf.api-status")


async def main() -> None:
    args = parse_cli_args("Chequeo de cuota API-Football sin tocar Supabase.")
    load_dotenv()

    api_key = os.getenv("API_SPORTS_KEY")
    if not api_key:
        raise RuntimeError("Falta API_SPORTS_KEY en el entorno.")

    settings = Settings(
        api_key=api_key,
        supabase_url="",
        supabase_service_role_key="",
        target_leagues=(),
        pinnacle_bookmaker_name="",
    )

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        payload = await api_client.fetch("status")

    data = (payload or {}).get("response") or payload or {}
    account = data.get("account") or {}
    subscription = data.get("subscription") or {}
    requests = data.get("requests") or {}

    LOGGER.info("Cuenta: %s", account.get("firstname") or account.get("email") or "desconocida")
    LOGGER.info("Plan activo: %s", subscription.get("active"))
    LOGGER.info("Requests usados hoy: %s / %s", requests.get("current"), requests.get("limit_day"))


if __name__ == "__main__":
    asyncio.run(main())
