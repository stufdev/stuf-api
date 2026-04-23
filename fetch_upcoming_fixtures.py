import os
import asyncio
import logging
from datetime import datetime, timedelta
import httpx
from supabase import create_client, Client
from dotenv import load_dotenv

# Configuración de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

API_KEY = os.getenv("API_SPORTS_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not all([API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    logger.error("Faltan variables de entorno necesarias (API_SPORTS_KEY, SUPABASE_URL, SUPABASE_KEY). Verifica tu archivo .env")
    exit(1)

# Inicializar cliente Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-apisports-key": API_KEY
}
TARGET_LEAGUES = [39, 140, 78, 135, 61]

async def fetch_data(client: httpx.AsyncClient, endpoint: str, params: dict = None) -> dict:
    """Peticiones a API-Football con manejo automático de Rate Limit (429)."""
    url = f"{BASE_URL}/{endpoint}"
    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = await client.get(url, headers=HEADERS, params=params)

            if response.status_code == 429:
                logger.warning(f"Rate Limit en {endpoint}. Esperando 8s (intento {attempt+1}/{max_retries})...")
                await asyncio.sleep(8)
                continue

            if response.status_code != 200:
                logger.error(f"Error {response.status_code} en {url}: {response.text}")
                return None

            data = response.json()

            if data.get("errors"):
                error_msg = str(data.get("errors"))
                if "rateLimit" in error_msg or "Too many requests" in error_msg:
                    logger.warning(f"Rate Limit en JSON de {endpoint}. Esperando 8s...")
                    await asyncio.sleep(8)
                    continue
                else:
                    logger.error(f"Error API en {url}: {data['errors']}")
                    return None

            return data

        except Exception as e:
            logger.error(f"Excepción en {url}: {e}")
            return None

    logger.error(f"Reintentos agotados para {endpoint}")
    return None

async def fetch_upcoming_fixtures(client: httpx.AsyncClient, date: str) -> list:
    """Obtiene los partidos programados (NS) para las ligas objetivo en una fecha futura."""
    logger.info(f"Obteniendo partidos programados para la fecha {date}...")
    all_fixtures = []
    
    # Obtenemos todos los partidos del día y filtramos por liga y estado (NS = Not Started)
    params = {"date": date, "status": "NS"}
    data = await fetch_data(client, "fixtures", params)
    
    if data and "response" in data:
        for item in data["response"]:
            league_id = item["league"]["id"]
            if league_id in TARGET_LEAGUES:
                all_fixtures.append(item)
                
    logger.info(f"Se encontraron {len(all_fixtures)} partidos programados (NS).")
    return all_fixtures

async def process_upcoming_fixture(fixture_data: dict):
    """Procesa e inserta datos básicos de teams y el fixture futuro."""
    fixture = fixture_data["fixture"]
    teams = fixture_data["teams"]
    
    fixture_id = fixture["id"]
    home_team = teams["home"]
    away_team = teams["away"]
    
    # Upsert Teams (Aseguramos que los equipos existan)
    for team in [home_team, away_team]:
        try:
            supabase.table("teams").upsert({
                "id": team["id"],
                "name": team["name"],
                "logo_url": team["logo"]
            }).execute()
        except Exception as e:
            logger.error(f"Error upsert team {team['id']}: {e}")

    # Upsert Fixture (Con goles en 0/null ya que no ha empezado)
    try:
        supabase.table("fixtures").upsert({
            "id": fixture_id,
            "league_id": fixture_data["league"]["id"],
            "season": fixture_data["league"]["season"],
            "date": fixture["date"],
            "home_team_id": home_team["id"],
            "away_team_id": away_team["id"],
            "home_goals": None,
            "away_goals": None,
            "home_goals_1h": None,
            "away_goals_1h": None,
            "status": fixture["status"]["short"] # Será "NS"
        }).execute()
        logger.info(f"Fixture Futuro {fixture_id} guardado correctamente.")
    except Exception as e:
        logger.error(f"Error upsert fixture {fixture_id}: {e}")

async def main():
    logger.info("Iniciando sincronización de Partidos Futuros (Próximos 5 días)")
    
    async with httpx.AsyncClient() as client:
        # Iteramos sobre los próximos 5 días, empezando por hoy
        for day_offset in range(6):
            target_date = (datetime.now() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            
            fixtures = await fetch_upcoming_fixtures(client, target_date)
            
            for fixture in fixtures:
                await process_upcoming_fixture(fixture)
                # Pausa ligera
                await asyncio.sleep(0.5)
            
            # Pausa entre días para respetar API
            await asyncio.sleep(2)
            
    logger.info("Sincronización de futuros finalizada correctamente.")

if __name__ == "__main__":
    asyncio.run(main())
