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

async def fetch_fixtures(client: httpx.AsyncClient, date: str) -> list:
    """Obtiene los partidos finalizados para las ligas objetivo en una fecha dada."""
    logger.info(f"Obteniendo partidos para la fecha {date}...")
    all_fixtures = []
    
    # Obtenemos todos los partidos del día y filtramos por liga y estado (FT = Final Time)
    params = {"date": date, "status": "FT"}
    data = await fetch_data(client, "fixtures", params)
    
    if data and "response" in data:
        for item in data["response"]:
            league_id = item["league"]["id"]
            if league_id in TARGET_LEAGUES:
                all_fixtures.append(item)
                
    logger.info(f"Se encontraron {len(all_fixtures)} partidos objetivo finalizados.")
    return all_fixtures

async def process_fixture_basic(fixture_data: dict):
    """Procesa e inserta/actualiza datos básicos de teams y fixtures."""
    fixture = fixture_data["fixture"]
    teams = fixture_data["teams"]
    goals = fixture_data["goals"]
    score = fixture_data["score"]
    
    fixture_id = fixture["id"]
    home_team = teams["home"]
    away_team = teams["away"]
    
    # Upsert Teams
    for team in [home_team, away_team]:
        try:
            supabase.table("teams").upsert({
                "id": team["id"],
                "name": team["name"],
                "logo_url": team["logo"] # Añadimos el logo que faltaba
            }).execute()
        except Exception as e:
            logger.error(f"Error upsert team {team['id']}: {e}")

    # Upsert Fixture (NOMBRES DE COLUMNAS CORREGIDOS SEGÚN EL ESQUEMA)
    try:
        supabase.table("fixtures").upsert({
            "id": fixture_id,
            "league_id": fixture_data["league"]["id"],
            "season": fixture_data["league"]["season"], # Añadimos temporada
            "date": fixture["date"],
            "home_team_id": home_team["id"],
            "away_team_id": away_team["id"],
            "home_goals": goals["home"],                # Corregido
            "away_goals": goals["away"],                # Corregido
            "home_goals_1h": score["halftime"]["home"], # Corregido
            "away_goals_1h": score["halftime"]["away"], # Corregido
            "status": fixture["status"]["short"]        # Añadimos status
        }).execute()
    except Exception as e:
        logger.error(f"Error upsert fixture {fixture_id}: {e}")

async def fetch_statistics(client: httpx.AsyncClient, fixture_id: int):
    """Obtiene las estadísticas del partido y hace upsert en fixture_statistics."""
    logger.info(f"Obteniendo estadísticas para el partido {fixture_id}...")
    data = await fetch_data(client, "fixtures/statistics", {"fixture": fixture_id})
    if not data or not data.get("response"):
        return

    for team_stats in data["response"]:
        team_id = team_stats["team"]["id"]
        stats_dict = {stat["type"]: stat["value"] for stat in team_stats["statistics"]}
        
        def get_stat(name, default=0):
            val = stats_dict.get(name)
            if val is None:
                return default
            if isinstance(val, str) and val.endswith('%'):
                return int(val.strip('%'))
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
                "booking_points": 0 # Se actualizará posteriormente con process_booking_points
            }, on_conflict="fixture_id, team_id, period").execute()
        except Exception as e:
            logger.error(f"Error upsert stats fixture {fixture_id} team {team_id}: {e}")

async def process_booking_points(fixture_id: int):
    """Calcula y actualiza los booking points en base a las tarjetas obtenidas de los eventos."""
    try:
        # Obtenemos los eventos de tarjeta del partido que acabamos de insertar
        events_resp = supabase.table("fixture_events")\
            .select("team_id, detail")\
            .eq("fixture_id", fixture_id)\
            .eq("type", "Card")\
            .execute()
        
        team_points = {}
        for event in events_resp.data:
            team_id = event["team_id"]
            detail = event["detail"]
            
            if team_id not in team_points:
                team_points[team_id] = 0
                
            if "Yellow" in detail:
                team_points[team_id] += 10
            elif "Red" in detail:
                team_points[team_id] += 25
                
        # Update a fixture_statistics con los booking points calculados
        for team_id, points in team_points.items():
            supabase.table("fixture_statistics")\
                .update({"booking_points": points})\
                .eq("fixture_id", fixture_id)\
                .eq("team_id", team_id)\
                .eq("period", "FT")\
                .execute()
                
    except Exception as e:
        logger.error(f"Error procesando booking points para fixture {fixture_id}: {e}")


async def fetch_events(client: httpx.AsyncClient, fixture_id: int):
    """Obtiene los eventos del partido, filtra por 'Card' y hace insert."""
    logger.info(f"Obteniendo eventos para el partido {fixture_id}...")
    data = await fetch_data(client, "fixtures/events", {"fixture": fixture_id})
    if not data or not data.get("response"):
        return

    for event in data["response"]:
        if event["type"] == "Card":
            try:
                # Nota: Se insertan eventos asumiendo que el ID es un UUID que se autogenera en la BD
                # si se configuró `default gen_random_uuid()` para la columna 'id' UUID en Supabase.
                supabase.table("fixture_events").insert({
                    "fixture_id": fixture_id,
                    "team_id": event["team"]["id"],
                    "type": event["type"],
                    "detail": event["detail"],
                    "minute": event["time"]["elapsed"]
                }).execute()
            except Exception as e:
                logger.error(f"Error insertando evento {fixture_id}: {e}")
                
    # Procesar booking points después de que los eventos estén en la BD
    await process_booking_points(fixture_id)

async def fetch_players(client: httpx.AsyncClient, fixture_id: int):
    """Obtiene las estadísticas individuales de los jugadores."""
    logger.info(f"Obteniendo estadísticas de jugadores para partido {fixture_id}...")
    data = await fetch_data(client, "fixtures/players", {"fixture": fixture_id})
    if not data or not data.get("response"):
        return

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
                logger.error(f"Error upsert player {player['id']} in fixture {fixture_id}: {e}")

async def fetch_predictions(client: httpx.AsyncClient, fixture_id: int):
    """Obtiene predicciones matemáticas (Poisson/+EV) y las guarda en fixture_predictions."""
    logger.info(f"Obteniendo predicciones para fixture {fixture_id}...")
    data = await fetch_data(client, "predictions", {"fixture": fixture_id})
    if not data or not data.get("response"):
        return

    prediction_data = data["response"][0]["predictions"]

    try:
        supabase.table("fixture_predictions").upsert({
            "fixture_id": fixture_id,
            "win_or_draw": prediction_data.get("win_or_draw", False),
            "under_over_line": str(prediction_data["under_over"]) if prediction_data.get("under_over") else None,
            "advice": prediction_data.get("advice", None)
        }).execute()
    except Exception as e:
        logger.error(f"Error upsert predictions fixture {fixture_id}: {e}")

async def process_fixture(client: httpx.AsyncClient, fixture_data: dict):
    """Orquesta la obtención de datos detallados para un partido."""
    fixture_id = fixture_data["fixture"]["id"]
    logger.info(f"--- Procesando Fixture {fixture_id} ---")

    await process_fixture_basic(fixture_data)

    await asyncio.sleep(3)
    await fetch_statistics(client, fixture_id)

    await asyncio.sleep(3)
    await fetch_events(client, fixture_id)

    await asyncio.sleep(3)
    await fetch_players(client, fixture_id)

    await asyncio.sleep(3)
    await fetch_predictions(client, fixture_id)

    logger.info(f"--- Fixture {fixture_id} completado ---")

import sys

async def main():
    # Determinamos la fecha a consultar. Permitimos pasarla por argumento o usamos "hoy" por defecto
    # El plan gratuito de API-Sports a menudo no permite fechas pasadas, por lo que usamos hoy.
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        target_date = datetime.now().strftime("%Y-%m-%d")
        
    logger.info(f"Iniciando sincronización de Motor de Ingesta para la fecha: {target_date}")
    
    async with httpx.AsyncClient() as client:
        fixtures = await fetch_fixtures(client, target_date)
        
        for fixture in fixtures:
            await process_fixture(client, fixture)
            # Pausa adicional entre partidos para no agotar cuotas rápidas
            await asyncio.sleep(1.5)
            
    logger.info("Sincronización finalizada correctamente.")

if __name__ == "__main__":
    asyncio.run(main())
