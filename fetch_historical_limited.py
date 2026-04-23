import os
import asyncio
import logging
from datetime import datetime
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
    logger.error("Faltan variables de entorno. Verifica tu archivo .env")
    exit(1)

# Inicializar cliente Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-apisports-key": API_KEY
}
# Para no agotar tu plan gratuito (100 peticiones/día), solo traeremos las 2 más populares
TARGET_LEAGUES = [39, 140] # Premier League, La Liga

async def fetch_data(client: httpx.AsyncClient, endpoint: str, params: dict = None) -> dict:
    """Peticiones a API-Football con manejo automático de Rate Limit."""
    url = f"{BASE_URL}/{endpoint}"
    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = await client.get(url, headers=HEADERS, params=params)

            if response.status_code == 429:
                logger.warning(f"Rate Limit en {endpoint}. Esperando 8s...")
                await asyncio.sleep(8)
                continue

            if response.status_code != 200:
                logger.error(f"Error {response.status_code} en {url}: {response.text}")
                return None

            data = response.json()

            if data.get("errors"):
                error_msg = str(data.get("errors"))
                if "rateLimit" in error_msg or "Too many requests" in error_msg or "requests limit" in error_msg:
                    logger.warning(f"Límite de peticiones alcanzado o Rate Limit en JSON. Esperando 10s...")
                    await asyncio.sleep(10)
                    continue
                else:
                    logger.error(f"Error API en {url}: {data['errors']}")
                    return None

            return data

        except Exception as e:
            logger.error(f"Excepción en {url}: {e}")
            return None

    return None

async def fetch_historical_fixtures(client: httpx.AsyncClient, league_id: int, season: int, limit: int = 15) -> list:
    """Obtiene los últimos N partidos finalizados de una liga."""
    logger.info(f"Consultando liga {league_id} temporada {season}...")
    params = {"league": league_id, "season": season, "status": "FT"}
    data = await fetch_data(client, "fixtures", params)
    
    if data and "response" in data:
        # Ordenar por fecha descendente (los más recientes primero)
        fixtures = sorted(data["response"], key=lambda x: x["fixture"]["timestamp"], reverse=True)
        # Tomar solo los últimos 'limit' partidos para no agotar la cuota gratuita
        selected = fixtures[:limit]
        logger.info(f"Seleccionados los últimos {len(selected)} partidos de la liga {league_id}.")
        return selected
    return []

async def process_fixture_basic(fixture_data: dict):
    fixture = fixture_data["fixture"]
    teams = fixture_data["teams"]
    goals = fixture_data["goals"]
    score = fixture_data["score"]
    
    fixture_id = fixture["id"]
    home_team = teams["home"]
    away_team = teams["away"]
    
    for team in [home_team, away_team]:
        try:
            supabase.table("teams").upsert({
                "id": team["id"],
                "name": team["name"],
                "logo_url": team["logo"]
            }).execute()
        except Exception as e:
            pass

    try:
        supabase.table("fixtures").upsert({
            "id": fixture_id,
            "league_id": fixture_data["league"]["id"],
            "season": fixture_data["league"]["season"],
            "date": fixture["date"],
            "home_team_id": home_team["id"],
            "away_team_id": away_team["id"],
            "home_goals": goals["home"],
            "away_goals": goals["away"],
            "home_goals_1h": score["halftime"]["home"],
            "away_goals_1h": score["halftime"]["away"],
            "status": fixture["status"]["short"]
        }).execute()
    except Exception as e:
        logger.error(f"Error upsert fixture {fixture_id}: {e}")

async def fetch_statistics(client: httpx.AsyncClient, fixture_id: int):
    data = await fetch_data(client, "fixtures/statistics", {"fixture": fixture_id})
    if not data or not data.get("response"): return

    for team_stats in data["response"]:
        team_id = team_stats["team"]["id"]
        stats_dict = {stat["type"]: stat["value"] for stat in team_stats["statistics"]}
        
        def get_stat(name, default=0):
            val = stats_dict.get(name)
            if val is None: return default
            if isinstance(val, str) and val.endswith('%'): return int(val.strip('%'))
            return val

        try:
            supabase.table("fixture_statistics").upsert({
                "fixture_id": fixture_id,
                "team_id": team_id,
                "period": "FT",
                "corners": get_stat("Corner Kicks"),
                "shots_on_target": get_stat("Shots on Goal"),
                "total_shots": get_stat("Total Shots"),
                "yellow_cards": get_stat("Yellow Cards"),
                "red_cards": get_stat("Red Cards"),
                "fouls": get_stat("Fouls"),
                "offsides": get_stat("Offsides"),
                "saves": get_stat("Goalkeeper Saves"),
                "booking_points": 0
            }, on_conflict="fixture_id, team_id, period").execute()
        except Exception as e:
            pass

async def process_booking_points(fixture_id: int):
    try:
        events_resp = supabase.table("fixture_events").select("team_id, detail").eq("fixture_id", fixture_id).eq("type", "Card").execute()
        team_points = {}
        for event in events_resp.data:
            team_id = event["team_id"]
            detail = event["detail"]
            if team_id not in team_points: team_points[team_id] = 0
            if "Yellow" in detail: team_points[team_id] += 10
            elif "Red" in detail: team_points[team_id] += 25
                
        for team_id, points in team_points.items():
            supabase.table("fixture_statistics").update({"booking_points": points}).eq("fixture_id", fixture_id).eq("team_id", team_id).eq("period", "FT").execute()
    except Exception as e:
        pass

async def fetch_events(client: httpx.AsyncClient, fixture_id: int):
    data = await fetch_data(client, "fixtures/events", {"fixture": fixture_id})
    if not data or not data.get("response"): return

    for event in data["response"]:
        if event["type"] == "Card":
            try:
                supabase.table("fixture_events").insert({
                    "fixture_id": fixture_id,
                    "team_id": event["team"]["id"],
                    "type": event["type"],
                    "detail": event["detail"],
                    "minute": event["time"]["elapsed"]
                }).execute()
            except Exception as e:
                pass
                
    await process_booking_points(fixture_id)

async def fetch_players(client: httpx.AsyncClient, fixture_id: int):
    data = await fetch_data(client, "fixtures/players", {"fixture": fixture_id})
    if not data or not data.get("response"): return

    for team_data in data["response"]:
        team_id = team_data["team"]["id"]
        for player_item in team_data["players"]:
            player = player_item["player"]
            stats = player_item["statistics"][0] if player_item["statistics"] else {}
            shots = stats.get("shots", {}) or {}
            fouls = stats.get("fouls", {}) or {}
            rating_str = stats.get("games", {}).get("rating")
            rating = float(rating_str) if rating_str else None

            try:
                supabase.table("fixture_player_stats").upsert({
                    "fixture_id": fixture_id,
                    "team_id": team_id,
                    "player_id": player["id"],
                    "player_name": player["name"],
                    "shots_on_target": shots.get("on", 0),
                    "fouls_committed": fouls.get("committed", 0),
                    "rating": rating
                }, on_conflict="fixture_id, player_id").execute()
            except Exception as e:
                pass

async def fetch_predictions(client: httpx.AsyncClient, fixture_id: int):
    data = await fetch_data(client, "predictions", {"fixture": fixture_id})
    if not data or not data.get("response"): return

    prediction_data = data["response"][0]["predictions"]
    try:
        supabase.table("fixture_predictions").upsert({
            "fixture_id": fixture_id,
            "win_or_draw": prediction_data.get("win_or_draw", False),
            "under_over_line": str(prediction_data["under_over"]) if prediction_data.get("under_over") else None,
            "advice": prediction_data.get("advice", None)
        }).execute()
    except Exception as e:
        pass

async def process_fixture(client: httpx.AsyncClient, fixture_data: dict):
    fixture_id = fixture_data["fixture"]["id"]
    logger.info(f"Procesando Fixture Histórico {fixture_id}...")
    
    await process_fixture_basic(fixture_data)
    
    # Pausas para el Rate Limit (100 peticiones al día en el plan gratis es el límite rudo)
    # 4 peticiones por partido = 25 partidos máximo al día.
    await asyncio.sleep(2)
    await fetch_statistics(client, fixture_id)
    
    await asyncio.sleep(2)
    await fetch_events(client, fixture_id)
    
    await asyncio.sleep(2)
    await fetch_players(client, fixture_id)
    
    await asyncio.sleep(2)
    await fetch_predictions(client, fixture_id)
    
    logger.info(f"Fixture {fixture_id} completado.")

async def main():
    logger.info("Iniciando Ingesta Histórica (Modo Seguro - Plan Gratuito)")
    # Temporada histórica permitida por el plan gratuito
    season = 2024 
    
    async with httpx.AsyncClient() as client:
        for league_id in TARGET_LEAGUES:
            # Traemos SOLO los últimos 12 partidos por liga para no quemar las 100 peticiones diarias
            # 12 partidos * 4 endpoints = 48 peticiones por liga. Total ~96 peticiones.
            fixtures = await fetch_historical_fixtures(client, league_id, season, limit=12)
            
            for fixture in fixtures:
                await process_fixture(client, fixture)
                await asyncio.sleep(3) # Pausa entre partidos
                
    logger.info("Ingesta Histórica Finalizada.")

if __name__ == "__main__":
    asyncio.run(main())
