# API-Football v3.9.3

**Base URL:** `https://v3.football.api-sports.io/`  
**Dashboard:** https://dashboard.api-football.com  
**Sitio:** https://www.api-football.com

---

## Autenticación

Solo peticiones **GET**. Único header permitido:

```
x-apisports-key: TU_API_KEY
```

> Frameworks como NodeJS pueden agregar headers extra automáticamente — elimínalos para evitar errores.

### Headers de respuesta
| Header | Descripción |
|---|---|
| `x-ratelimit-requests-limit` | Requests permitidos por día |
| `x-ratelimit-requests-remaining` | Requests restantes del día |
| `X-RateLimit-Limit` | Máx. llamadas por minuto |
| `X-RateLimit-Remaining` | Llamadas restantes del minuto |

**Rate limit:** Si excedes el límite por minuto, tu acceso puede bloquearse temporal o permanentemente.

### Códigos de respuesta
| Código | Descripción |
|---|---|
| 200 | OK |
| 204 | Sin contenido (parámetros válidos pero sin datos) |
| 499 | Timeout |
| 500 | Error interno del servidor |

### Estructura de respuesta
```json
{
  "get": "endpoint_name",
  "parameters": {},
  "errors": [],
  "results": 1,
  "paging": { "current": 1, "total": 1 },
  "response": [...]
}
```

---

## Endpoint: Status / Cuenta

```
GET /status
```
No cuenta contra la cuota diaria.

```python
import requests
url = "https://v3.football.api-sports.io/status"
headers = {"x-apisports-key": "TU_API_KEY"}
r = requests.get(url, headers=headers)
print(r.json())
```

```js
fetch("https://v3.football.api-sports.io/status", {
  method: "GET",
  headers: { "x-apisports-key": "TU_API_KEY" }
}).then(r => r.json()).then(console.log)
```

**Respuesta incluye:** `account` (nombre, email), `subscription` (plan, activa, vence), `requests` (current, limit_day).

---

## Logos / Imágenes

Las llamadas a logos **no cuentan** contra la cuota diaria, pero están sujetas a rate limit por segundo/minuto. Se recomienda cachear localmente o usar un CDN (BunnyCDN).

- Logo de liga: `https://media.api-sports.io/football/leagues/{league_id}.png`
- Logo de equipo: `https://media.api-sports.io/football/teams/{team_id}.png`
- Foto de coach: `https://media.api-sports.io/football/coachs/{coach_id}.png`
- Bandera de país: `https://media.api-sports.io/flags/{country_code}.svg`

---

## Arquitectura de datos

```
Seasons ──────────────────── Countries
              │
           Leagues
    ┌─────────┼───────────────────────────────┐
   H2H    Fixtures   Live  Odds  Standings  Venues  Top Scorers  Players  Teams
    │         │        │     │                                     │        │
Predictions Events  Live    Pre-match                          Stats    Seasons
 Injuries  Lineups  Odds     Odds                             Squads  Countries
           Stats            Bets                             Profiles  Stats
                         Bookmakers                          Transfers Trophies
                                                             Sidelined  Coaches
```

---

## Sample scripts

### Python (`requests`)
```python
import requests

url = "https://v3.football.api-sports.io/leagues"
headers = {"x-apisports-key": "TU_API_KEY"}
payload = {}

response = requests.get(url, headers=headers, data=payload)
print(response.text)
```

### JavaScript (Fetch)
```js
const myHeaders = new Headers()
myHeaders.append("x-apisports-key", "TU_API_KEY")

fetch("https://v3.football.api-sports.io/leagues", {
  method: "GET",
  headers: myHeaders,
  redirect: "follow"
})
.then(r => r.text())
.then(console.log)
.catch(e => console.log("error", e))
```

### Node.js (Axios)
```js
const axios = require("axios")

axios({
  method: "get",
  url: "https://v3.football.api-sports.io/leagues",
  headers: { "x-apisports-key": "TU_API_KEY" }
})
.then(r => console.log(JSON.stringify(r.data)))
.catch(console.log)
```

---

## Endpoints

> Todos los endpoints usan `GET` y requieren el header `x-apisports-key`.  
> `*required*` indica parámetro obligatorio.

---

### Timezone
```
GET /timezone
```
Lista de timezones válidos para usar en `/fixtures`. Sin parámetros.  
**Update:** estático | **Calls recomendadas:** 1 cuando se necesite.

---

### Countries
```
GET /countries
```
Lista de países disponibles para el endpoint `/leagues`.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `name` | string | Nombre del país |
| `code` | string [2–6 chars] | Código alpha (FR, GB…) |
| `search` | string [≥3 chars] | Búsqueda por nombre |

**Update:** al agregar nuevas ligas | **Calls recomendadas:** 1/día.

```python
# Ejemplos
requests.get(url + "countries", headers=h)
requests.get(url + "countries?name=england", headers=h)
requests.get(url + "countries?code=fr", headers=h)
requests.get(url + "countries?search=engl", headers=h)
```

---

### Leagues / Seasons
```
GET /leagues
GET /leagues/seasons
```

**`/leagues`** — Lista de ligas y copas disponibles. El `id` de liga es único y persiste entre temporadas.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `id` | integer | ID de la liga |
| `name` | string | Nombre |
| `country` | string | País |
| `code` | string [2–6] | Código alpha |
| `season` | integer [4 chars] | Temporada (YYYY) |
| `team` | integer | ID del equipo |
| `type` | string | `"league"` o `"cup"` |
| `current` | string | `"true"` / `"false"` — temporada activa |
| `search` | string [≥3 chars] | Nombre o país |
| `last` | integer [≤2 chars] | Últimas X ligas añadidas |

**Update:** al agregar ligas | **Calls recomendadas:** 1/día.

**`/leagues/seasons`** — Lista de todas las temporadas disponibles. Sin parámetros.

```python
requests.get(url + "leagues?id=39", headers=h)
requests.get(url + "leagues?season=2019&country=england&type=league", headers=h)
requests.get(url + "leagues?team=85&season=2019", headers=h)
requests.get(url + "leagues/seasons", headers=h)
```

---

### Teams
```
GET /teams
GET /teams/statistics
GET /teams/seasons
GET /teams/countries
```

**`/teams`** — Información de equipos.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `id` | integer | ID del equipo |
| `name` | string | Nombre |
| `league` | integer | ID de liga |
| `season` | integer | Temporada (YYYY) |
| `country` | string | País |
| `code` | string | Código del equipo (3 letras) |
| `venue` | integer | ID del estadio |
| `search` | string [≥3] | Búsqueda por nombre o país |

**`/teams/statistics`** — Estadísticas de un equipo en una liga/temporada.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `league` | integer *required* | ID de liga |
| `season` | integer *required* | Temporada (YYYY) |
| `team` | integer *required* | ID del equipo |
| `date` | string YYYY-MM-DD | Fecha límite |

**`/teams/seasons`** — Temporadas disponibles para un equipo.  
Parámetro: `team` (integer, *required*)

**`/teams/countries`** — Países disponibles para el endpoint teams. Sin parámetros.

**Update:** diario | **Calls recomendadas:** 1/día.

```python
requests.get(url + "teams?id=33", headers=h)
requests.get(url + "teams?league=39&season=2019", headers=h)
requests.get(url + "teams/statistics?league=39&team=33&season=2019", headers=h)
requests.get(url + "teams/seasons?team=33", headers=h)
```

---

### Venues
```
GET /venues
```

| Parámetro | Tipo | Descripción |
|---|---|---|
| `id` | integer | ID del estadio |
| `name` | string | Nombre |
| `city` | string | Ciudad |
| `country` | string | País |
| `search` | string [≥3] | Nombre, ciudad o país |

**Update:** al agregar equipos | **Calls recomendadas:** 1/día.

```python
requests.get(url + "venues?id=556", headers=h)
requests.get(url + "venues?city=manchester", headers=h)
requests.get(url + "venues?search=trafford", headers=h)
```

---

### Standings
```
GET /standings
```

| Parámetro | Tipo | Descripción |
|---|---|---|
| `league` | integer | ID de liga |
| `season` | integer *required* | Temporada (YYYY) |
| `team` | integer | ID del equipo |

**Update:** diario | **Calls recomendadas:** 1/día.

```python
requests.get(url + "standings?league=39&season=2019", headers=h)
requests.get(url + "standings?league=39&team=33&season=2019", headers=h)
```

---

### Fixtures / Rounds

```
GET /fixtures/rounds
GET /fixtures
GET /fixtures/headtohead
```

#### Fixture statuses

| Short | Long | Tipo |
|---|---|---|
| TBD | Time To Be Defined | Not Played |
| NS | Not Started | Not Played |
| 1H | First Half | In Play |
| HT | Halftime | In Play |
| 2H | Second Half | In Play |
| ET | Extra Time | In Play |
| BT | Break Time | In Play |
| P | Penalty In Progress | In Play |
| SUSP | Match Suspended | In Play |
| INT | Match Interrupted | In Play |
| FT | Match Finished | Finished |
| AET | After Extra Time | Finished |
| PEN | Finished Penalties | Finished |
| PST | Match Postponed | Postponed |
| CANC | Match Cancelled | Cancelled |
| ABD | Match Abandoned | Abandoned |
| AWD | Technical Loss | Not Played |
| WO | WalkOver | Not Played |
| LIVE | In Progress | In Play |

> - IDs de fixtures son únicos y nunca cambian.
> - Datos actualizados cada **15 segundos**.
> - No todas las competiciones tienen livescore; en ese caso `status` permanece en `NS` hasta 48h tras el partido.

**`/fixtures/rounds`**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `league` | integer *required* | ID de liga |
| `season` | integer *required* | Temporada (YYYY) |
| `current` | boolean | Solo la jornada actual |
| `dates` | boolean | Incluir fechas de cada jornada |
| `timezone` | string | Timezone válido |

**`/fixtures`**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `id` | integer | ID del fixture |
| `ids` | string | Varios IDs: `id-id-id` (máx. 20) |
| `live` | string | `"all"` o IDs de ligas: `"39-61"` |
| `date` | string YYYY-MM-DD | Fecha |
| `league` | integer | ID de liga |
| `season` | integer | Temporada (YYYY) |
| `team` | integer | ID del equipo |
| `last` | integer [≤2] | Últimos X fixtures |
| `next` | integer [≤2] | Próximos X fixtures |
| `from` | string YYYY-MM-DD | Desde fecha |
| `to` | string YYYY-MM-DD | Hasta fecha |
| `round` | string | Jornada (ej. `"Regular Season - 1"`) |
| `status` | string | Estado(s): `"NS"` o `"NS-PST-FT"` |
| `venue` | integer | ID del estadio |
| `timezone` | string | Timezone válido |

**Update:** cada 15s | **Calls recomendadas:** 1/min si hay fixtures en curso, sino 1/día.

**`/fixtures/headtohead`**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `h2h` | string *required* | IDs de equipos: `"ID-ID"` |
| `date` | string YYYY-MM-DD | Fecha |
| `league` | integer | ID de liga |
| `season` | integer | Temporada (YYYY) |
| `last` | integer | Últimos X fixtures |
| `next` | integer | Próximos X fixtures |
| `from` / `to` | string | Rango de fechas |
| `status` | string | Estado(s) |
| `venue` | integer | ID del estadio |

```python
# Fixtures en juego
requests.get(url + "fixtures?live=all", headers=h)
# Fixtures de una liga/temporada
requests.get(url + "fixtures?league=39&season=2019", headers=h)
# Fixture por ID (incluye events, lineups, stats, players)
requests.get(url + "fixtures?id=215662", headers=h)
# Múltiples IDs
requests.get(url + "fixtures?ids=215662-215663-215664", headers=h)
# H2H
requests.get(url + "fixtures/headtohead?h2h=33-34", headers=h)
```

---

### Fixtures: Statistics
```
GET /fixtures/statistics
```
Estadísticas de un fixture por equipo.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `fixture` | integer *required* | ID del fixture |
| `team` | integer | ID del equipo |
| `type` | string | Tipo de estadística |
| `half` | boolean | Incluir stats de 1er tiempo (desde 2024) |

**Estadísticas disponibles:** Shots On/Off Goal, Total/Blocked Shots, Shots Inside/Outside Box, Fouls, Corner Kicks, Offsides, Ball Possession, Yellow/Red Cards, Goalkeeper Saves, Total/Accurate Passes, Passes %.

**Update:** cada 15s | **Calls recomendadas:** 1/min en curso, sino 1/día.

```python
requests.get(url + "fixtures/statistics?fixture=215662&team=463", headers=h)
```

---

### Fixtures: Events
```
GET /fixtures/events
```

| Parámetro | Tipo | Descripción |
|---|---|---|
| `fixture` | integer *required* | ID del fixture |
| `team` | integer | ID del equipo |
| `player` | integer | ID del jugador |
| `type` | string | Tipo de evento |

**Tipos de eventos:**
- `Goal`: Normal Goal, Own Goal, Penalty, Missed Penalty
- `Card`: Yellow Card, Red Card
- `Subst`: Substitution [1, 2, 3…]
- `Var`: Goal Cancelled, Penalty Confirmed (desde 2020-2021)

**Update:** cada 15s | **Calls recomendadas:** 1/min en curso, sino 1/día.

```python
requests.get(url + "fixtures/events?fixture=215662", headers=h)
requests.get(url + "fixtures/events?fixture=215662&type=card", headers=h)
```

---

### Fixtures: Lineups
```
GET /fixtures/lineups
```

| Parámetro | Tipo | Descripción |
|---|---|---|
| `fixture` | integer *required* | ID del fixture |
| `team` | integer | ID del equipo |
| `player` | integer | ID del jugador |
| `type` | string | Tipo |

Incluye formación, titulares, suplentes y colores de camiseta. Posición en campo: eje X desde portería, eje Y de izquierda a derecha.

**Update:** cada 15 min | **Calls recomendadas:** 1/15min en curso, sino 1/día.

```python
requests.get(url + "fixtures/lineups?fixture=592872", headers=h)
```

---

### Fixtures: Players Statistics
```
GET /fixtures/players
```

| Parámetro | Tipo | Descripción |
|---|---|---|
| `fixture` | integer *required* | ID del fixture |
| `team` | integer | ID del equipo |

**Update:** cada 1 min | **Calls recomendadas:** 1/min en curso, sino 1/día.

```python
requests.get(url + "fixtures/players?fixture=169080", headers=h)
```

---

### Injuries
```
GET /injuries
```
Jugadores no disponibles (suspendidos, lesionados). Datos desde abril 2021.

Tipos: `Missing Fixture` (no jugará) / `Questionable` (dudoso).

| Parámetro | Tipo | Descripción |
|---|---|---|
| `league` | integer | ID de liga |
| `season` | integer | Temporada (YYYY) |
| `fixture` | integer | ID del fixture |
| `ids` | string | Varios fixture IDs: `"id-id-id"` |
| `team` | integer | ID del equipo |
| `player` | integer | ID del jugador |
| `date` | string YYYY-MM-DD | Fecha |
| `timezone` | string | Timezone válido |

**Update:** diario | **Calls recomendadas:** 1/día.

```python
requests.get(url + "injuries?league=2&season=2020", headers=h)
requests.get(url + "injuries?fixture=686314", headers=h)
requests.get(url + "injuries?team=85&season=2020", headers=h)
requests.get(url + "injuries?date=2021-04-07", headers=h)
```

---

### Predictions
```
GET /predictions
```
Predicciones para un fixture usando estadísticas históricas y algoritmo propio (sin odds de casas de apuestas).

| Parámetro | Tipo | Descripción |
|---|---|---|
| `fixture` | integer *required* | ID del fixture |

**Predicciones disponibles:**
- `winner`: ID del equipo que puede ganar
- `win_or_draw`: `true` si puede ganar o empatar
- `under_over`: -1.5 / -2.5 / -3.5 / -4.5 / +1.5 / +2.5 / +3.5 / +4.5
- `goals_home` / `goals_away`: -1.5 / -2.5 / -3.5 / -4.5
- `advice`: consejo textual (ej. "Deportivo Santani or draws and -3.5 goals")
- Comparativas: Strength, Attacking/Defensive potential, Poisson distribution, H2H strength/goals, Wins.

> `*` → valor negativo = máximo de goals. Ej. `-1.5` = máximo 1 gol.

**Update:** cada hora | **Calls recomendadas:** 1/hora en curso, sino 1/día.

```python
requests.get(url + "predictions?fixture=198772", headers=h)
```

---

### Coachs
```
GET /coachs
```
Información y carrera de entrenadores.  
Foto: `https://media.api-sports.io/football/coachs/{coach_id}.png`

| Parámetro | Tipo | Descripción |
|---|---|---|
| `id` | integer | ID del coach |
| `team` | integer | ID del equipo |
| `search` | string [≥3] | Búsqueda por nombre |

**Update:** diario | **Calls recomendadas:** 1/día.

```python
requests.get(url + "coachs?team=85", headers=h)
requests.get(url + "coachs?search=guardiola", headers=h)
```

---

### Players

#### Players Seasons
```
GET /players/seasons
```
Temporadas disponibles para estadísticas de jugadores.  
Parámetro opcional: `player` (integer).

#### Players (Statistics)
```
GET /players
```

| Parámetro | Tipo | Descripción |
|---|---|---|
| `id` | integer | ID del jugador |
| `team` | integer | ID del equipo |
| `league` | integer | ID de liga |
| `season` | integer *required* (con id/league/team) | Temporada (YYYY) |
| `search` | string [≥4] | Nombre (requiere league o team) |
| `page` | integer (default 1) | Paginación (250 resultados/página) |

**Update:** diario | **Calls recomendadas:** 1/día.

```python
requests.get(url + "players?id=19088&season=2018", headers=h)
requests.get(url + "players?team=85&season=2020", headers=h)
requests.get(url + "players?league=39&season=2019&search=salah", headers=h)
```

#### Players Profiles
```
GET /players/profiles
```
Perfil de jugador sin estadísticas de temporada.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `player` | integer | ID del jugador |
| `search` | string | Apellido (≥3 chars) |
| `page` | integer | Paginación |

```python
requests.get(url + "players/profiles?player=276", headers=h)
requests.get(url + "players/profiles?search=ney", headers=h)
```

#### Players Squads
```
GET /players/squads
```
Plantilla actual de un equipo, o equipos de un jugador.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `team` | integer | ID del equipo |
| `player` | integer | ID del jugador |

**Update:** varias veces/semana | **Calls recomendadas:** 1/semana.

#### Players Teams
```
GET /players/teams
```
Todos los equipos de un jugador.  
Parámetro: `player` (integer *required*).

#### Top Scorers
```
GET /players/topscorers
```
Top 20 goleadores de una liga/temporada.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `league` | integer *required* | ID de liga |
| `season` | integer *required* | Temporada (YYYY) |

Criterio de desempate: 1-Goles, 2-Asistencias, 3-Partidos jugados, 4-Minutos jugados.

#### Top Assists
```
GET /players/topassists
```
Top 20 asistentes. Mismos parámetros que topscorers.  
Desempate: 1-Asistencias, 2-Goles, 3-Partidos, 4-Minutos.

#### Top Yellow Cards
```
GET /players/topyellowcards
```
Top 20 tarjetas amarillas. Mismos parámetros.  
Desempate: 1-Amarillas, 2-Rojas, 3-Asistencias, 4-Menos minutos jugados.

#### Top Red Cards
```
GET /players/topredcards
```
Top 20 tarjetas rojas. Mismos parámetros.  
Desempate: 1-Rojas, 2-Amarillas, 3-Asistencias, 4-Menos minutos.

**Update:** varias veces/semana | **Calls recomendadas:** 1/día.

```python
requests.get(url + "players/topscorers?league=39&season=2019", headers=h)
requests.get(url + "players/topassists?league=61&season=2020", headers=h)
```

---

### Transfers
```
GET /transfers
```
Transferencias de jugadores y equipos.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `player` | integer | ID del jugador |
| `team` | integer | ID del equipo |

**Update:** varias veces/semana | **Calls recomendadas:** 1/día.

```python
requests.get(url + "transfers?player=35845", headers=h)
requests.get(url + "transfers?team=85", headers=h)
```

---

### Trophies
```
GET /trophies
```
Todos los trofeos de un jugador o entrenador.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `player` | integer | ID del jugador |
| `coach` | integer | ID del coach |
| `players` | string | Varios IDs: `"id-id"` |
| `coachs` | string | Varios IDs: `"id-id"` |

**Update:** varias veces/semana | **Calls recomendadas:** 1/día.

```python
requests.get(url + "trophies?player=276", headers=h)
requests.get(url + "trophies?coach=2", headers=h)
requests.get(url + "trophies?players=276-278-279", headers=h)
```

---

### Sidelined
```
GET /sidelined
```
Historial de ausencias por lesión/suspensión de un jugador o entrenador.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `player` | integer | ID del jugador |
| `coach` | integer | ID del coach |
| `players` | string | Varios IDs: `"id-id-id"` |
| `coachs` | string | Varios IDs: `"id-id-id"` |

```python
requests.get(url + "sidelined?player=276", headers=h)
requests.get(url + "sidelined?players=276-278-279-280-281", headers=h)
requests.get(url + "sidelined?coach=2", headers=h)
```

---

## Odds (Live)

### odds/live
```
GET /odds/live
```
Cuotas en tiempo real para fixtures en curso.

- Fixtures se añaden 5-15 min antes del inicio.
- Fixtures se eliminan 5-20 min después de terminar.
- **No se guarda historial.**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `fixture` | integer | ID del fixture |
| `league` | integer | ID de liga |
| `bet` | integer | ID del tipo de apuesta |

**Update:** cada 5s (puede variar 5–60s).

**Status fields en respuesta:**
- `stopped`: árbitro ha detenido el partido temporalmente
- `blocked`: apuestas bloqueadas temporalmente
- `finished`: fixture no iniciado o finalizado

```python
requests.get(url + "odds/live?fixture=721238", headers=h)
requests.get(url + "odds/live?league=39", headers=h)
requests.get(url + "odds/live?bet=4&fixture=164327", headers=h)
```

### odds/live/bets
```
GET /odds/live/bets
```
Lista de apuestas disponibles para in-play. **No compatibles** con el endpoint `odds` (pre-match).  
Sin parámetros. **Update:** cada 60s.

---

## Odds (Pre-Match)

### odds
```
GET /odds
```
Cuotas pre-partido. Paginación: 10 resultados/página.  
Disponibles entre 1 y 14 días antes del partido. Historial de 7 días.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `fixture` | integer | ID del fixture |
| `league` | integer | ID de liga |
| `season` | integer | Temporada (YYYY) |
| `date` | string YYYY-MM-DD | Fecha |
| `timezone` | string | Timezone válido |
| `page` | integer | Página (default 1) |
| `bookmaker` | integer | ID del bookmaker |
| `bet` | integer | ID del tipo de apuesta |

**Update:** cada 3h | **Calls recomendadas:** 1/3h.

```python
requests.get(url + "odds?fixture=326090&bookmaker=6", headers=h)
requests.get(url + "odds?league=39&season=2019&bet=4", headers=h)
requests.get(url + "odds?date=2020-05-15&page=2&bet=4", headers=h)
```

> **Campo `main`:** Se establece en `true` cuando existen múltiples valores idénticos para la misma apuesta — indica la apuesta a considerar. Si el valor es único, `main` siempre es `false` o `null`.

### odds/mapping
```
GET /odds/mapping
```
Lista de fixtures `id` disponibles para el endpoint `odds`. Paginación: 100 resultados/página.  
Parámetro: `page` (integer).

### odds/bookmakers
```
GET /odds/bookmakers
```
Lista de bookmakers disponibles. Sus `id` se usan en `odds` como filtro.  
Sin parámetros requeridos. **Update:** varias veces/semana | **Calls recomendadas:** 1/día.

### odds/bets
```
GET /odds/bets
```
Lista de tipos de apuesta para pre-match. Sus `id` se usan en `odds`.  
**No compatibles** con `odds/live`.  
Sin parámetros requeridos. **Update:** varias veces/semana.

```python
requests.get(url + "odds/bookmakers", headers=h)
requests.get(url + "odds/bets", headers=h)
```

---

## Resumen de todos los endpoints

| Endpoint | Método | Descripción |
|---|---|---|
| `/status` | GET | Cuenta y cuota |
| `/timezone` | GET | Timezones válidos |
| `/countries` | GET | Lista de países |
| `/leagues` | GET | Ligas y copas |
| `/leagues/seasons` | GET | Temporadas disponibles |
| `/teams` | GET | Información de equipos |
| `/teams/statistics` | GET | Estadísticas de equipo |
| `/teams/seasons` | GET | Temporadas de un equipo |
| `/teams/countries` | GET | Países disponibles en teams |
| `/venues` | GET | Estadios |
| `/standings` | GET | Clasificaciones |
| `/fixtures/rounds` | GET | Jornadas de una liga |
| `/fixtures` | GET | Partidos (livescore, histórico, próximos) |
| `/fixtures/headtohead` | GET | H2H entre dos equipos |
| `/fixtures/statistics` | GET | Stats de un partido |
| `/fixtures/events` | GET | Eventos de un partido |
| `/fixtures/lineups` | GET | Alineaciones |
| `/fixtures/players` | GET | Stats de jugadores en partido |
| `/injuries` | GET | Bajas por lesión/suspensión |
| `/predictions` | GET | Predicción de partido |
| `/coachs` | GET | Entrenadores |
| `/players/seasons` | GET | Temporadas de jugadores |
| `/players` | GET | Estadísticas de jugadores |
| `/players/profiles` | GET | Perfil de jugador |
| `/players/squads` | GET | Plantillas |
| `/players/teams` | GET | Equipos de un jugador |
| `/players/topscorers` | GET | Top goleadores |
| `/players/topassists` | GET | Top asistentes |
| `/players/topyellowcards` | GET | Top tarjetas amarillas |
| `/players/topredcards` | GET | Top tarjetas rojas |
| `/transfers` | GET | Transferencias |
| `/trophies` | GET | Trofeos |
| `/sidelined` | GET | Historial de ausencias |
| `/odds/live` | GET | Cuotas en vivo |
| `/odds/live/bets` | GET | Tipos de apuesta live |
| `/odds` | GET | Cuotas pre-partido |
| `/odds/mapping` | GET | Fixtures con odds disponibles |
| `/odds/bookmakers` | GET | Bookmakers |
| `/odds/bets` | GET | Tipos de apuesta pre-match |
