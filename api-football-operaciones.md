# API-Football - Manual Operativo de Ingesta

Estado: alineado al codigo del repo  
Fecha de actualizacion: 2026-05-19

## 1. Principios

- No se hardcodean IDs de bookmakers ni ligas auxiliares que la API ya publica en catalogos.
- Antes de abrir fan-out contra sub-endpoints costosos, se consulta Supabase para evitar rehidratar fixtures ya cerrados.
- La cobertura de cada liga/temporada se resuelve desde `/leagues` y se persiste en `league_coverage`.
- Las cuotas pre-match se capturan en batch por `date + bookmaker`, no por fixture uno a uno.
- El frontend consume datos analiticos via rutas server-side; no consulta tablas privadas con el cliente publico.

## 2. Tablas base nuevas

- `leagues`: catalogo de competiciones.
- `league_coverage`: flags oficiales por `league_id + season`.
- `api_reference_bookmakers`: catalogo oficial de bookmakers.
- `api_reference_bets`: catalogo oficial de mercados live y pre-match.
- `fixture_lineups`: alineaciones publicadas.
- `fixture_odds_snapshots`: snapshots JSONB de odds por fixture y bookmaker.

## 3. Carriles operativos

### Carril A - Historico Seguro

Script: `fetch_historical_limited.py`  
Alias: `bulk_historical_ingestion.py`

- Descarga fixtures finalizados por `league + season`.
- Respeta `FT`, `AET` y `PEN`.
- Omite fan-out si el fixture ya esta cerrado y con detalle hidratado.
- Usa coverage real para decidir si vale la pena llamar `statistics`, `events`, `players` y `predictions`.

### Carril A2 - Cierre de Brecha Reciente

Script temporal: `fetch_recent_window.py`

- Rehidrata solo fixtures finalizados dentro de una ventana reciente.
- Ideal para rellenar el hueco cuando el cierre nocturno no corrió por varios dias.
- Reusa exactamente la misma hidratacion de detalle de `fetch_historical_limited.py`.
- Por defecto trabaja sobre los ultimos `20` dias, incluyendo hoy UTC.
- No recalcula agregados por si mismo; despues se corren los rebuilds normales.

Caso recomendado:

```powershell
cd C:\stuf-stadistics\stuf-api

python check_api_status.py --request-delay 1.0

python fetch_recent_window.py --leagues 140,39,61,78,135 --season 2025 --days-back 20 --skip-predictions --request-delay 1.0

python rebuild_player_season_stats.py --leagues 140,39,61,78,135 --season 2025

python rebuild_stat_averages.py --leagues 140,39,61,78,135 --season 2025

python rebuild_trend_engine.py --leagues 140,39,61,78,135 --season 2025

python rebuild_referee_stats.py --leagues 140,39,61,78,135 --season 2025

python fetch_upcoming_fixtures.py --leagues 140,39,61,78,135 --days 6 --skip-predictions --request-delay 1.0

python check_api_status.py --request-delay 1.0
```

Variantes utiles:

- Ahorrar API si sabes que parte de la ventana ya esta bien:

```powershell
python fetch_recent_window.py --leagues 140 --season 2025 --days-back 20 --skip-known --skip-predictions --request-delay 1.0
```

- Fijar una fecha final concreta:

```powershell
python fetch_recent_window.py --leagues 140 --season 2025 --date 2026-05-15 --days-back 20 --skip-predictions --request-delay 1.0
```

### Carril B - Cierre Nocturno

Script: `sync_football_data.py`

- Consulta una sola vez `/fixtures?date=YYYY-MM-DD`.
- Filtra ligas objetivo en backend.
- Actualiza status reales, incluyendo `PST`, `CANC`, `ABD`, `SUSP`, `INT`.
- Solo hace fan-out para fixtures finales que no esten ya totalmente hidratados.

Horario recomendado:
- 04:00 UTC para "ayer".

### Carril C - Planning

Script: `fetch_upcoming_fixtures.py`

- Recorre los proximos `N` dias con `/fixtures?date=...&status=NS-TBD`.
- Persiste fixtures futuros.
- Llama `/predictions?fixture=...` solo si la liga tiene coverage y la prediccion falta o esta vieja.

Horario recomendado:
- 05:00 UTC.

### Carril D - Lineups

Script: `fetch_lineups_hotzone.py`

- Busca fixtures en ventana caliente de 90 minutos.
- Llama `/fixtures/lineups?fixture=...` solo si la liga soporta lineups y aun no hay datos guardados.
- La logica de scheduler recomendada es lanzar este job varias veces cerca del kickoff, no una sola bala fija.

Cadencia recomendada:
- T-35, T-20, T-10 minutos.

### Carril D - Odds Pre-match

Script: `fetch_pre_match_odds.py`

- Resuelve el bookmaker desde `api_reference_bookmakers`.
- Agrupa fixtures por fecha y llama `/odds?date=...&bookmaker=...` con paginacion.
- Guarda snapshots JSONB por fixture en `fixture_odds_snapshots`.
- No consulta cada 15 minutos por defecto porque la API actualiza odds pre-match cada 3 horas.

Cadencia recomendada:
- cada 3 horas.

## 4. Reglas de negocio ya aterrizadas

### Booking points

- Amarilla = 10
- Roja = 25

Implementacion:
- Se borran primero los eventos `Card` del fixture.
- Luego se insertan de nuevo desde la API.
- El recalculo de `booking_points` parte de esa base limpia.

### Integridad de periodos

- `fixture_statistics` mantiene el campo `period`.
- El repo persiste `FT` y, cuando API-Football lo soporta, tambien `1H`; `2H` se deriva de `FT - 1H` para stats aditivas.
- El frontend puede leer `FT`, `1H` y `2H` sin mezclar granularidades.

### Predicciones

- No se asume que `predictions` venga "gratis" con planning.
- Cada fixture requiere su propia llamada `/predictions`.
- El refresh se restringe: si faltan datos o si el partido esta cerca y la fila esta vieja.

## 5. Presupuesto recomendado

Supuesto de sabado fuerte:

- Sync nocturno: ~150 req
- Planning 5 dias: ~5 req
- Predictions de 30 fixtures: ~30 req
- Lineups para 30 fixtures con 2 chequeos efectivos: ~60 req
- Odds pre-match batcheadas: ~24 req

Total esperado:
- ~269 req/dia, muy por debajo del plan Pro de 7,500.

## 6. Bootstrap P0 recomendado

Objetivo: llenar datos reales suficientes para probar `Comparison` sin gastar cuota de mas.

Alcance inicial recomendado:

- Liga: La Liga (`league_id=140`)
- Temporada: `2025`
- Historico: 100-120 fixtures finalizados
- Futuros: hoy + 5 dias
- Fan-out: `fixtures/statistics` + `fixtures/events`
- Postergar: players, predictions, odds y lineups

Orden seguro:

```powershell
cd C:\stuf-stadistics\stuf-api

python check_api_status.py --request-delay 1.0

python fetch_historical_limited.py --leagues 140 --season 2025 --limit 120 --skip-players --skip-predictions --request-delay 1.0

python rebuild_stat_averages.py --leagues 140 --season 2025

python rebuild_trend_engine.py --leagues 140 --season 2025

python rebuild_referee_stats.py --leagues 140 --season 2025

python fetch_upcoming_fixtures.py --leagues 140 --days 6 --skip-predictions --request-delay 1.0
```

Presupuesto aproximado para 120 fixtures:

- Catalogos base: ~4 requests
- Fixtures historicos por liga/temporada: 1 request
- Statistics + events: ~240 requests
- Upcoming 6 dias: ~6 requests
- Total esperado: ~250-270 requests

Reglas:

- No correr varios backfills en paralelo.
- Mantener `--request-delay 1.0` mientras no sepamos el limite exacto por minuto.
- Usar `--leagues` en cada corrida para no depender del default amplio de `TARGET_LEAGUES`.
- Activar players/predictions solo cuando cerremos el modulo correspondiente.

### Bootstrap fase Player Stats

Cuando toque habilitar `Player Stats`, el siguiente bootstrap sobre la misma liga/temporada debe incluir players y recalcular sus agregados:

```powershell
cd C:\\stuf-stadistics\\stuf-api

python check_api_status.py --request-delay 1.0

python fetch_historical_limited.py --leagues 140 --season 2025 --limit 120 --skip-predictions --request-delay 1.0

python rebuild_player_season_stats.py --leagues 140 --season 2025

python rebuild_stat_averages.py --leagues 140 --season 2025

python rebuild_trend_engine.py --leagues 140 --season 2025

python rebuild_referee_stats.py --leagues 140 --season 2025

python fetch_upcoming_fixtures.py --leagues 140 --days 6 --skip-predictions --request-delay 1.0
```

Notas:

- `fixture_events` ya debe persistir goles y tarjetas para soportar `1st Goals`, `1H Goals` y `2H Goals`.
- `player_fixture_stats` alimenta el leaderboard base.
- `player_season_stats` queda como agregado rapido para `overall/home/away`.

### Acceso correcto para Player Stats

`Player Stats` debe leerse por la ruta interna `stuf-web/app/api/v1/comparison/player-stats/route.ts`.

- El navegador no debe abrir politicas `anon` sobre tablas analiticas.
- La ruta server-side usa credenciales privadas y devuelve solo el payload agregado que consume el panel.
- `player_fixture_stats`, `player_season_stats` y `fixture_events` deben permanecer privados.

### Bootstrap fase Referee Stats

`Referee Stats` no requiere llamadas nuevas a API si ya existen `fixtures`, `fixture_statistics` y `fixture_events`. Se reconstruye desde Supabase:

```powershell
cd C:\\stuf-stadistics\\stuf-api

python rebuild_referee_stats.py --leagues 140 --season 2025
```

Este rebuild:

- normaliza `fixtures.referee_name_raw` hacia `referees`
- completa `fixtures.referee_id`
- recalcula `referee_fixture_facts`
- recalcula `referee_market_stats`

### Auditoria de arbitros duplicados

Para detectar pronto casos donde el proveedor parta el mismo arbitro en varios nombres o ids aparentes:

```powershell
cd C:\\stuf-stadistics\\stuf-api

python audit_referee_duplicates.py --leagues 140 --season 2025
```

El script:

- agrupa arbitros por alias heuristico, por ejemplo `F. Maeso` vs `Francisco Hernandez Maeso`
- recomienda el candidato canónico
- muestra cuantos `fixtures`, `referee_fixture_facts` y `referee_market_stats` tiene cada id
- marca residuos como `ORPHAN_AGGREGATES` cuando un id viejo ya no tiene fixtures pero aun conserva agregados

### Acceso correcto para Referee Stats

`Referee Stats` debe leerse por la ruta interna `stuf-web/app/api/v1/comparison/referee-stats/route.ts`.

- El navegador no debe abrir politicas `anon` sobre `referee_fixture_facts` ni `referee_market_stats`.
- La resolucion del arbitro visible en `Comparison` y su analitica deben pasar por la capa server-side.
- Si el panel devuelve `-`, el problema ya no es RLS publico: hay que revisar datos faltantes, referee canonico o fallos del pipeline.

## 7. Variables de entorno

```env
API_SPORTS_KEY=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
TARGET_LEAGUES=39,61,78,135,140
PINNACLE_BOOKMAKER_NAME=Pinnacle
```

## 8. Secuencia recomendada de despliegue

1. Aplicar el SQL nuevo en Supabase.
2. Correr el Bootstrap P0 recomendado para una liga.
3. Programar `sync_football_data.py` y `fetch_upcoming_fixtures.py`.
4. Programar `fetch_lineups_hotzone.py` y `fetch_pre_match_odds.py`.
5. Validar en frontend que las rutas internas resuelvan `fixtures`, `leagues` y `fixture_statistics` con `FT/1H/2H` cuando aplique.
