# API-Football - Manual Canonico de STUF

Estado: alineado al codigo del repo
Fecha de actualizacion: 2026-05-20

Este archivo es la unica fuente de verdad en `stuf-api` para:

- fundamentos de la API oficial
- flujo logico de consulta
- reglas de cuota y cache
- mapeo a tablas y scripts del repo
- recetas operativas de bootstrap, gap fill y jobs recurrentes

## 1. Alcance

Este manual no intenta copiar toda la documentacion publica de API-Football. Resume solo lo que STUF usa o debe usar.

Cobertura del manual:

- descubrimiento de catalogos
- fixtures y sub-endpoints costosos
- players, injuries, predictions y odds
- estrategia de persistencia en Supabase
- operacion real del pipeline del repo

## 2. Fundamentos de la API

Base URL:

```txt
https://v3.football.api-sports.io/
```

Dashboard:

- [API-Football Dashboard](https://dashboard.api-football.com)
- [API-Football](https://www.api-football.com)

Autenticacion:

- todos los endpoints son `GET`
- el unico header requerido es:

```txt
x-apisports-key: TU_API_KEY
```

Notas:

- algunos clientes agregan headers extra; si la API protesta, reducirlos al minimo
- `/status` no consume cuota diaria
- logos e imagenes no consumen cuota diaria, pero si tienen rate limit

### 2.1 Headers relevantes de respuesta

| Header | Significado |
|---|---|
| `x-ratelimit-requests-limit` | limite diario |
| `x-ratelimit-requests-remaining` | remanente diario |
| `X-RateLimit-Limit` | limite por minuto |
| `X-RateLimit-Remaining` | remanente del minuto |

### 2.2 Codigos de respuesta

| Codigo | Significado |
|---|---|
| `200` | OK |
| `204` | parametros validos pero sin datos |
| `499` | timeout |
| `500` | error interno |

### 2.3 Forma canonica de respuesta

```json
{
  "get": "endpoint_name",
  "parameters": {},
  "errors": [],
  "results": 1,
  "paging": { "current": 1, "total": 1 },
  "response": []
}
```

## 3. Flujo logico de consulta

Orden recomendado de descubrimiento:

```txt
/timezone
  -> /countries
  -> /leagues
  -> /leagues/seasons
  -> /teams, /standings, /fixtures/rounds
  -> /fixtures
  -> /fixtures/statistics, /fixtures/events, /fixtures/lineups, /fixtures/players
  -> /injuries, /predictions
  -> /odds, /odds/bookmakers, /odds/bets
```

Flujos minimos utiles:

- resultados: `/leagues -> /fixtures?live=all -> /fixtures/events + /fixtures/statistics`
- prediccion: `/leagues -> /fixtures?next=10 -> /predictions?fixture=... + /odds?fixture=...`
- perfil de jugador: `/players/profiles -> /players -> /trophies + /transfers + /sidelined`

## 4. IDs que STUF debe persistir

| ID | Origen | Uso |
|---|---|---|
| `league_id` | `/leagues` | fixtures, standings, players, odds |
| `season` | `/leagues`, `/leagues/seasons` | clave de coverage y agregados |
| `team_id` | `/teams` | fixtures, stats, players, injuries |
| `fixture_id` | `/fixtures` | todos los sub-endpoints de partido |
| `player_id` | `/players` | stats, profiles, trophies, transfers |
| `venue_id` | `/venues` | contexto de estadio |
| `bookmaker_id` | `/odds/bookmakers` | odds pre-match |
| `bet_id` | `/odds/bets` | mercados pre-match |
| `live_bet_id` | `/odds/live/bets` | mercados live |

Notas clave:

- `league_id` es estable entre temporadas
- `fixture_id` es unico y no cambia
- las temporadas se representan como `YYYY`
- `/players` pagina a 250
- `/odds` pagina a 10
- `/odds/mapping` pagina a 100
- `odds/live` no guarda historial; desaparece poco despues del fin del partido
- `odds/live/bets` y `odds/bets` no comparten IDs

## 5. Mapa de endpoints que importan a STUF

### 5.1 Descubrimiento y catalogos

| Endpoint | Parametros clave | Update oficial | Uso en STUF |
|---|---|---|---|
| `/status` | ninguno | sin cuota | health check y control de cuota |
| `/timezone` | ninguno | estatico | soporte de fechas si hace falta |
| `/countries` | `name`, `code`, `search` | muy poco frecuente | catalogo de paises |
| `/leagues` | `id`, `country`, `season`, `team`, `type`, `current`, `search` | al agregar ligas | catalogo canonico de competiciones y coverage |
| `/leagues/seasons` | ninguno | poco frecuente | validacion de temporadas |
| `/venues` | `id`, `name`, `city`, `country`, `search` | muy poco frecuente | catalogo de estadios |

Punto importante:

- STUF resuelve la cobertura real por `league_id + season` desde `/leagues` y la persiste en `league_coverage`

### 5.2 Contexto de competicion y equipo

| Endpoint | Parametros clave | Update oficial | Uso en STUF |
|---|---|---|---|
| `/teams` | `id`, `league`, `season`, `country`, `venue`, `search` | diario | catalogo de equipos |
| `/teams/statistics` | `league`, `season`, `team`, `date` | diario | referencia externa; STUF prefiere agregados propios |
| `/teams/seasons` | `team` | diario | temporadas por equipo |
| `/teams/countries` | ninguno | bajo cambio | catalogo auxiliar |
| `/standings` | `season` y `league` o `team` | diario | tabla de posiciones |
| `/fixtures/rounds` | `league`, `season`, `current`, `dates` | diario | jornadas |

### 5.3 Fixtures y detalle de partido

Estados de fixture que STUF usa:

| Grupo | Codes |
|---|---|
| Upcoming | `NS`, `TBD` |
| In Play | `1H`, `HT`, `2H`, `ET`, `BT`, `P`, `SUSP`, `INT`, `LIVE` |
| Final | `FT`, `AET`, `PEN` |
| No jugado / invalido para analitica | `PST`, `CANC`, `ABD`, `AWD`, `WO` |

| Endpoint | Parametros clave | Update oficial | Uso en STUF |
|---|---|---|---|
| `/fixtures` | `id`, `ids`, `live`, `date`, `league`, `season`, `team`, `last`, `next`, `from`, `to`, `status` | cada 15s en vivo | shell de fixtures, historico, upcoming, cierre nocturno |
| `/fixtures/headtohead` | `h2h`, `last`, `next`, `from`, `to` | frecuente | contexto H2H si se necesita |
| `/fixtures/statistics` | `fixture`, `team`, `type`, `half` | cada 15s en vivo | stats FT; con `half=true` la API mantiene `statistics` como FT y agrega `statistics_1h` / `statistics_2h` por equipo |
| `/fixtures/events` | `fixture`, `team`, `player`, `type` | cada 15s en vivo | goles, tarjetas, cambios, VAR |
| `/fixtures/lineups` | `fixture`, `team`, `player` | cada 15 min | formacion y once inicial |
| `/fixtures/players` | `fixture`, `team` | cada 1 min | player fixture stats |
| `/injuries` | `league`, `season`, `fixture`, `team`, `player`, `date` | diario | bajas y disponibilidad |
| `/predictions` | `fixture` | cada hora | prediccion previa al partido |

Notas de negocio:

- `fixtures/statistics?half=true` no reemplaza `statistics`; agrega `statistics_1h` y `statistics_2h` dentro de cada equipo
- `fixtures/events` es la base de booking points, first goals y cortes por tiempo
- `fixtures/players` es costoso; solo debe abrirse donde coverage lo justifica
- `predictions` no viene embebido en `fixtures`; cada fixture requiere su propia llamada

### 5.4 Players, coaches y rosters

| Endpoint | Parametros clave | Update oficial | Uso en STUF |
|---|---|---|---|
| `/coachs` | `id`, `team`, `search` | diario | catalogo auxiliar de entrenadores |
| `/players/seasons` | `player` | diario | temporadas disponibles |
| `/players` | `season` + `id` o `league` o `team`; `page` | diario | stats de jugador por temporada |
| `/players/profiles` | `player` o `search`; `page` | diario | perfil sin stats |
| `/players/squads` | `team` o `player` | varias veces por semana | plantillas |
| `/players/teams` | `player` | varias veces por semana | historial de equipos |
| `/players/topscorers` | `league`, `season` | varias veces por semana | tops |
| `/players/topassists` | `league`, `season` | varias veces por semana | tops |
| `/players/topyellowcards` | `league`, `season` | varias veces por semana | tops |
| `/players/topredcards` | `league`, `season` | varias veces por semana | tops |
| `/transfers` | `player` o `team` | varias veces por semana | historial de transferencias |
| `/trophies` | `player` o `coach` | varias veces por semana | historial de titulos |
| `/sidelined` | `player` | varias veces por semana | ausencias historicas |

Regla importante:

- en `/players`, `season` es obligatoria cuando consultas por estadisticas

### 5.5 Odds

| Endpoint | Parametros clave | Update oficial | Uso en STUF |
|---|---|---|---|
| `/odds/live` | `fixture`, `league`, `bet` | 5s a 60s | fuera del pipeline actual principal |
| `/odds/live/bets` | ninguno | ~60s | catalogo solo para live odds |
| `/odds` | `fixture`, `league`, `season`, `date`, `page`, `bookmaker`, `bet` | ~3h | capturas pre-match |
| `/odds/mapping` | `page` | frecuente | fixtures con odds disponibles |
| `/odds/bookmakers` | ninguno | varias veces por semana | catalogo oficial de bookmakers |
| `/odds/bets` | ninguno | varias veces por semana | catalogo oficial de mercados pre-match |

Notas:

- STUF usa `api_reference_bookmakers` y `api_reference_bets` para no hardcodear IDs
- odds pre-match existen normalmente entre 1 y 14 dias antes del fixture
- `odds` tiene historial corto; por eso STUF guarda snapshots JSONB

## 6. Politica de cache y ahorro de cuota

| Dato | Endpoint | Cache recomendada |
|---|---|---|
| cuota y salud | `/status` | bajo demanda |
| timezones | `/timezone` | indefinido |
| paises | `/countries` | 7 dias |
| ligas y seasons | `/leagues`, `/leagues/seasons` | 24h |
| equipos | `/teams` | 24h |
| standings | `/standings` | 1-2h |
| venues | `/venues` | 7 dias |
| fixtures no live | `/fixtures` | 1h |
| fixtures live | `/fixtures?live=all` | 15-30s |
| fixture stats / events | `/fixtures/statistics`, `/fixtures/events` | 15-30s live, 1h post-match |
| lineups | `/fixtures/lineups` | desde T-90, luego reintentos |
| player fixture stats | `/fixtures/players` | 60s live, 1h post-match |
| injuries | `/injuries` | 6h |
| predictions | `/predictions` | 1h |
| odds pre-match | `/odds` | 3h |
| bookmakers / bets | `/odds/bookmakers`, `/odds/bets` | 24h |

Errores tipicos:

- llamar `/countries` y `/leagues` en cada request
- hacer polling de `/fixtures?live=all` cada segundo
- pedir lineups horas antes del kickoff
- usar `/players` sin `season`
- mezclar `odds/bets` con `odds/live/bets`

## 7. Como STUF aterriza la API en el repo

### 7.1 Principios de arquitectura

- `supported_leagues` es la configuracion canonica del producto
- `--leagues` es override manual para bootstrap o corridas puntuales
- `TARGET_LEAGUES` queda solo como override tecnico opcional
- antes de abrir fan-out costoso se revisa si el fixture ya esta hidratado
- `league_coverage` manda sobre lo que vale la pena consultar
- odds pre-match se capturan en batch por `date + bookmaker`
- el frontend consume payloads server-side; no abre tablas analiticas al cliente publico

### 7.2 Tablas base que soportan la ingesta

| Tabla | Rol |
|---|---|
| `supported_leagues` | configuracion canonica de ligas soportadas |
| `leagues` | catalogo de competiciones |
| `league_seasons` | temporadas por liga |
| `league_coverage` | flags oficiales por liga y temporada |
| `teams` | catalogo de equipos |
| `fixtures` | shell principal de fixtures |
| `fixture_statistics` | stats FT y 1H |
| `fixture_events` | eventos de partido |
| `fixture_lineups` | alineaciones |
| `player_fixture_stats` | stats por jugador en fixture |
| `fixture_predictions_api` | respuesta de predictions |
| `api_reference_bookmakers` | catalogo oficial de bookmakers |
| `api_reference_bets` | catalogo oficial de mercados |
| `fixture_odds_snapshots` | snapshots JSONB de odds |

### 7.3 Reglas ya aterrizadas

Booking points:

- amarilla = 10
- roja = 25

Integridad de periodos:

- STUF persiste `FT`
- si `half=true` trae `statistics_1h`, STUF persiste `1H`
- si `half=true` trae `statistics_2h`, STUF persiste `2H`; si faltara, `2H` puede derivarse para estadisticas aditivas desde `FT - 1H`

Predictions:

- se refrescan solo si faltan o si el kickoff esta cerca y la fila esta vieja

## 8. Carriles operativos del pipeline

### 8.1 Carril A - Historico

Script principal:

- `fetch_historical_limited.py`

Alias historico:

- `bulk_historical_ingestion.py`

Capacidades actuales:

- modo por `--limit` para ultimos X fixtures finales
- modo por `--days-back` para todos los fixtures finalizados dentro de una ventana reciente
- usa `FT`, `AET`, `PEN`
- omite fan-out si el fixture ya esta sano
- usa coverage real para decidir `statistics`, `events`, `players`, `predictions`

### 8.2 Carril A2 - Cierre de brecha reciente

Helper explicito:

- `fetch_recent_window.py`

Uso:

- rehidrata una ventana reciente
- comparte la misma hidratacion de detalle que `fetch_historical_limited.py`
- no recalcula agregados; despues van rebuilds

Nota:

- hoy el camino preferido es usar `fetch_historical_limited.py --days-back ...`
- `fetch_recent_window.py` sigue siendo util como helper explicito

### 8.3 Carril B - Cierre nocturno

Script:

- `sync_football_data.py`

Funcion:

- una llamada a `/fixtures?date=YYYY-MM-DD`
- filtra ligas objetivo en backend
- actualiza estados reales
- solo hace fan-out para finales no hidratados

Horario sugerido:

- `04:00 UTC` para procesar "ayer"

### 8.4 Carril C - Planning

Script:

- `fetch_upcoming_fixtures.py`

Funcion:

- recorre proximos `N` dias
- persiste fixtures `NS` y `TBD`
- llama `/predictions` solo cuando hace falta

Horario sugerido:

- `05:00 UTC`

### 8.5 Carril D - Lineups

Script:

- `fetch_lineups_hotzone.py`

Funcion:

- busca fixtures en ventana caliente de 90 minutos
- llama `/fixtures/lineups` solo si coverage lo permite y aun no hay datos

Cadencia sugerida:

- `T-35`, `T-20`, `T-10`

### 8.6 Carril E - Odds pre-match

Script:

- `fetch_pre_match_odds.py`

Funcion:

- resuelve bookmaker desde catalogo
- agrupa fixtures por fecha
- consulta `/odds?date=...&bookmaker=...` con paginacion
- guarda snapshots por fixture

Cadencia sugerida:

- cada 3 horas

## 9. Recetas operativas

### 9.1 Health check

```powershell
cd C:\stuf\stuf-api
python check_api_status.py --request-delay 1.0
```

Debe devolver cuenta, plan activo y cuota diaria.

### 9.2 Bootstrap P0 para Comparison

Objetivo:

- una liga
- una temporada
- suficiente historico real
- minimo gasto de cuota

Caso recomendado:

```powershell
cd C:\stuf\stuf-api

python check_api_status.py --request-delay 1.0

python fetch_historical_limited.py --leagues 140 --season 2025 --limit 120 --skip-players --skip-predictions --request-delay 1.0

python rebuild_stat_averages.py --leagues 140 --season 2025

python rebuild_trend_engine.py --leagues 140 --season 2025

python rebuild_referee_stats.py --leagues 140 --season 2025

python fetch_upcoming_fixtures.py --leagues 140 --days 6 --skip-predictions --request-delay 1.0
```

### 9.3 Bootstrap con Player Stats

```powershell
cd C:\stuf\stuf-api

python check_api_status.py --request-delay 1.0

python fetch_historical_limited.py --leagues 140 --season 2025 --limit 120 --skip-predictions --request-delay 1.0

python rebuild_player_season_stats.py --leagues 140 --season 2025

python rebuild_stat_averages.py --leagues 140 --season 2025

python rebuild_trend_engine.py --leagues 140 --season 2025

python rebuild_referee_stats.py --leagues 140 --season 2025

python fetch_upcoming_fixtures.py --leagues 140 --days 6 --skip-predictions --request-delay 1.0
```

### 9.4 Tapar un hueco reciente de N dias

Ejemplo: ultimos 10 dias hasta `2026-05-20`.

```powershell
cd C:\stuf\stuf-api

python fetch_historical_limited.py --leagues 39,61,78,135,140 --season 2025 --days-back 10 --date 2026-05-20 --skip-predictions --request-delay 1.0

python rebuild_player_season_stats.py --leagues 39,61,78,135,140 --season 2025

python rebuild_stat_averages.py --leagues 39,61,78,135,140 --season 2025

python rebuild_trend_engine.py --leagues 39,61,78,135,140 --season 2025

python rebuild_referee_stats.py --leagues 39,61,78,135,140 --season 2025
```

Si quieres limitar la cantidad dentro de la ventana:

```powershell
python fetch_historical_limited.py --leagues 140 --season 2025 --days-back 10 --date 2026-05-20 --limit 30 --skip-predictions --request-delay 1.0
```

### 9.5 Gap fill explicito con helper

```powershell
cd C:\stuf\stuf-api

python fetch_recent_window.py --leagues 140,39,61,78,135 --season 2025 --days-back 20 --skip-predictions --request-delay 1.0

python rebuild_player_season_stats.py --leagues 140,39,61,78,135 --season 2025

python rebuild_stat_averages.py --leagues 140,39,61,78,135 --season 2025

python rebuild_trend_engine.py --leagues 140,39,61,78,135 --season 2025

python rebuild_referee_stats.py --leagues 140,39,61,78,135 --season 2025

python fetch_upcoming_fixtures.py --leagues 140,39,61,78,135 --days 6 --skip-predictions --request-delay 1.0
```

### 9.6 Referee stats y auditoria

Rebuild:

```powershell
cd C:\stuf\stuf-api
python rebuild_referee_stats.py --leagues 140 --season 2025
```

Auditoria:

```powershell
cd C:\stuf\stuf-api
python audit_referee_duplicates.py --leagues 140 --season 2025
```

## 10. Variables de entorno

```env
API_SPORTS_KEY=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
PINNACLE_BOOKMAKER_NAME=Pinnacle
# TARGET_LEAGUES=39,61,78,135,140
```

Notas:

- `TARGET_LEAGUES` no es la fuente canonica del producto
- usar `--leagues` para ejecuciones manuales puntuales
- la fuente canonica del producto es `supported_leagues`
- en el estado actual de `stuf-api`, el cliente Python de Supabase valida JWT; por eso el backend usa la clave legacy `service_role` JWT, no `sb_secret_*`

## 11. Checklist de despliegue

1. Aplicar schema en Supabase.
2. Si la base ya existia, aplicar `schema/002_supported_leagues.sql`.
3. Configurar `supported_leagues`.
4. Correr bootstrap P0 para una liga.
5. Programar `sync_football_data.py` y `fetch_upcoming_fixtures.py`.
6. Programar `fetch_lineups_hotzone.py` y `fetch_pre_match_odds.py`.
7. Validar frontend contra rutas server-side.

## 12. Resumen rapido de endpoints

| Endpoint | Rol |
|---|---|
| `/status` | salud y cuota |
| `/timezone` | catalogo auxiliar |
| `/countries` | catalogo de paises |
| `/leagues` | catalogo de ligas y coverage |
| `/leagues/seasons` | temporadas disponibles |
| `/teams` | catalogo de equipos |
| `/teams/statistics` | referencia externa por equipo |
| `/teams/seasons` | temporadas por equipo |
| `/teams/countries` | catalogo auxiliar |
| `/venues` | catalogo de estadios |
| `/standings` | tabla de posiciones |
| `/fixtures/rounds` | jornadas |
| `/fixtures` | historico, live, futuros |
| `/fixtures/headtohead` | H2H |
| `/fixtures/statistics` | stats de partido |
| `/fixtures/events` | eventos de partido |
| `/fixtures/lineups` | alineaciones |
| `/fixtures/players` | stats de jugadores por fixture |
| `/injuries` | bajas |
| `/predictions` | prediccion API |
| `/coachs` | entrenadores |
| `/players/seasons` | temporadas de jugador |
| `/players` | stats de jugador |
| `/players/profiles` | perfil |
| `/players/squads` | plantillas |
| `/players/teams` | historial de equipos |
| `/players/topscorers` | top goleadores |
| `/players/topassists` | top asistencias |
| `/players/topyellowcards` | top amarillas |
| `/players/topredcards` | top rojas |
| `/transfers` | transferencias |
| `/trophies` | trofeos |
| `/sidelined` | ausencias historicas |
| `/odds/live` | cuotas live |
| `/odds/live/bets` | catalogo de mercados live |
| `/odds` | cuotas pre-match |
| `/odds/mapping` | fixtures con odds |
| `/odds/bookmakers` | catalogo de bookmakers |
| `/odds/bets` | catalogo de mercados pre-match |
