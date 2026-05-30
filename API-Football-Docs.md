# API-Football v3.9.3

> **Soporte:** https://dashboard.api-football.com  
> **Sitio web:** https://www.api-football.com

---

## Tabla de contenido

1. [Introducción](#introducción)
2. [Autenticación](#autenticación)
3. [Account Status](#account-status)
4. [Headers de respuesta](#headers-de-respuesta)
5. [Rate Limiting](#rate-limiting)
6. [Logos e Imágenes](#logos-e-imágenes)
7. [Scripts de ejemplo](#scripts-de-ejemplo)
8. [Widgets](#widgets)
9. [Endpoints](#endpoints)
   - [Timezone](#timezone)
   - [Countries](#countries)
   - [Leagues](#leagues)
   - [Seasons](#seasons)
   - [Teams](#teams)
   - [Teams Statistics](#teams-statistics)
   - [Teams Seasons](#teams-seasons)
   - [Teams Countries](#teams-countries)
   - [Venues](#venues)
   - [Standings](#standings)
   - [Fixtures / Rounds](#fixtures--rounds)
   - [Fixtures](#fixtures)
   - [Head to Head](#head-to-head)
   - [Fixtures Statistics](#fixtures-statistics)
   - [Fixtures Events](#fixtures-events)
   - [Fixtures Lineups](#fixtures-lineups)
   - [Fixtures Players](#fixtures-players)
   - [Injuries](#injuries)
   - [Predictions](#predictions)
   - [Coachs](#coachs)
   - [Players Seasons](#players-seasons)
   - [Players Profiles](#players-profiles)
   - [Players Statistics](#players-statistics)
   - [Players Squads](#players-squads)
   - [Players Teams](#players-teams)
   - [Top Scorers](#top-scorers)
   - [Top Assists](#top-assists)
   - [Top Yellow Cards](#top-yellow-cards)
   - [Top Red Cards](#top-red-cards)
   - [Transfers](#transfers)
   - [Trophies](#trophies)
   - [Sidelined](#sidelined)
   - [Odds (In-Play)](#odds-in-play)
   - [Odds (Pre-Match)](#odds-pre-match)
   - [Odds Mapping](#odds-mapping)
   - [Bookmakers](#bookmakers)
   - [Bets](#bets)
10. [Changelog](#changelog)

---

## Introducción

API-Football permite acceder a información completa sobre ligas, copas, equipos, jugadores, partidos y cuotas de todo el mundo.

**Base URL:**
```
https://v3.football.api-sports.io/
```

> La frecuencia de actualización indicada en la documentación es orientativa y puede variar según la competición.

---

## Autenticación

Todos los requests deben incluir tu API key en el **header** de la solicitud.

```
x-apisports-key: TU_API_KEY
```

> Regístrate en el [dashboard](https://dashboard.api-football.com) para obtener tu API key.

### Restricciones

- La API solo acepta solicitudes **GET**.
- Solo se permiten los headers listados (`x-apisports-key`).
- Algunos frameworks (especialmente en JS/Node) añaden headers extra automáticamente — asegúrate de eliminarlos.

---

## Account Status

Consulta el estado de tu cuenta (plan, consumo, límites). **Esta llamada no cuenta contra tu cuota diaria.**

```
GET /status
```

**Respuesta de ejemplo:**
```json
{
  "get": "status",
  "parameters": [],
  "errors": [],
  "results": 1,
  "response": {
    "account": {
      "firstname": "John",
      "lastname": "Doe",
      "email": "john@example.com"
    },
    "subscription": {
      "plan": "Free",
      "end": "2024-12-31T23:59:59+00:00",
      "active": true
    },
    "requests": {
      "current": 12,
      "limit_day": 100
    }
  }
}
```

---

## Headers de respuesta

Cada respuesta incluye los siguientes headers:

| Header | Descripción |
|--------|-------------|
| `x-ratelimit-requests-limit` | Número de requests asignados por día según tu plan |
| `x-ratelimit-requests-remaining` | Requests restantes del día |
| `X-RateLimit-Limit` | Máximo de llamadas por minuto |
| `X-RateLimit-Remaining` | Llamadas restantes antes de alcanzar el límite por minuto |

---

## Rate Limiting

Si excedes la tasa de requests permitida por minuto —ya sea por uso excesivo continuo o picos de tráfico anormales— tu acceso puede ser bloqueado temporal o permanentemente por el firewall sin previo aviso.

---

## Logos e Imágenes

Las llamadas a logos/imágenes **no cuentan** para tu cuota diaria y son gratuitas. Sin embargo, están sujetas a límites por segundo/minuto. Se recomienda almacenar estas imágenes en tu propio sistema (CDN) para no afectar la experiencia del usuario.

> Se recomienda usar una CDN como [BunnyCDN](https://bunny.net). Hay un tutorial disponible en el blog oficial.

**Aviso legal:** Los logos, imágenes y marcas entregadas a través de la API se proporcionan únicamente con fines de identificación descriptiva. La API no es propietaria de estos activos visuales. El uso de este contenido puede requerir autorización adicional de los titulares de derechos correspondientes.

---

## Scripts de ejemplo

Reemplaza `{endpoint}` por el nombre real del endpoint (ej: `leagues`, `fixtures`) y `YOUR_API_KEY` por tu clave.

### Python

```python
import requests

url = "https://v3.football.api-sports.io/{endpoint}"

headers = {
    "x-apisports-key": "YOUR_API_KEY"
}

response = requests.get(url, headers=headers)
print(response.json())
```

### JavaScript (Fetch)

```javascript
const headers = new Headers();
headers.append("x-apisports-key", "YOUR_API_KEY");

fetch("https://v3.football.api-sports.io/{endpoint}", {
  method: "GET",
  headers: headers,
  redirect: "follow"
})
  .then(response => response.json())
  .then(data => console.log(data))
  .catch(error => console.error("Error:", error));
```

---

## Widgets

Los widgets de API-SPORTS permiten mostrar datos deportivos dinámicos en tu web sin necesidad de un framework. Funcionan con todos los planes, incluyendo el gratuito.

**Características:**
- Ultra-modulares: cada componente es autónomo
- Personalizables: idioma, tema, contenido, comportamiento
- Fácil integración: solo un tag HTML

### Seguridad

Tu API key es visible en el código fuente del widget. Para protegerla:
1. Limita los dominios/IPs permitidos desde el dashboard.
2. Resetea tu API key y activa la restricción de dominios.
3. Sigue el [tutorial oficial](https://dashboard.api-football.com) para ocultar completamente tu key.

### Caché

Sin caché, cada visita genera un request a la API. Implementar una caché de solo 60 segundos puede reducir el consumo de 115 200 a solo 1 440 requests/día.

### Temas disponibles

```html
<!-- Temas: white (default), grey, dark, blue -->
<api-sports-widget data-type="config"
  data-key="YOUR_API_KEY"
  data-sport="football"
  data-theme="dark"
  data-lang="es">
</api-sports-widget>
```

### Widgets disponibles

| Widget | Descripción |
|--------|-------------|
| `games` | Lista de partidos |
| `game` | Detalle de un partido |
| `team` | Perfil de equipo |
| `player` | Perfil de jugador |
| `standings` | Tabla de posiciones |
| `league` | Calendario de liga |
| `leagues` | Lista de todas las ligas |
| `h2h` | Head-to-head histórico |
| `races`, `race`, `driver` | Fórmula 1 |
| `fights`, `fight`, `fighter` | MMA |

### Targeting dinámico

```html
<!-- Abrir widget en un contenedor -->
<api-sports-widget data-type="games"></api-sports-widget>
<div id="details"></div>
<api-sports-widget data-type="config"
  data-key="YOUR_API_KEY"
  data-sport="football"
  data-target-game="#details">
</api-sports-widget>

<!-- Abrir widget en modal -->
<api-sports-widget data-type="config"
  data-key="YOUR_API_KEY"
  data-sport="football"
  data-target-game="modal">
</api-sports-widget>
```

---

## Endpoints

### Estructura de respuesta común

Todos los endpoints retornan la misma estructura base:

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

**Códigos de respuesta:**

| Código | Descripción |
|--------|-------------|
| `200` | OK |
| `204` | Sin contenido |
| `499` | Timeout |
| `500` | Error interno del servidor |

---

### Timezone

Obtiene la lista de zonas horarias disponibles para usar en el endpoint `fixtures`.

```
GET /timezone
```

**Parámetros:** Ninguno requerido.

**Update Frequency:** Estático, no se actualiza.  
**Llamadas recomendadas:** 1 vez cuando se necesite.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/timezone",
    headers={"x-apisports-key": "YOUR_API_KEY"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/timezone", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

**Respuesta de ejemplo:**
```json
{
  "results": 425,
  "response": ["Africa/Abidjan", "Africa/Accra", "Europe/London", "America/New_York"]
}
```

---

### Countries

Obtiene la lista de países disponibles para el endpoint `leagues`.

```
GET /countries
```

Los campos `name` y `code` pueden usarse como filtros en otros endpoints.  
Flag URL: `https://media.api-sports.io/flags/{country_code}.svg`

**Parámetros query (opcionales):**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `name` | string | Nombre del país |
| `code` | string [2-6 chars] | Código Alpha (FR, GB-ENG…) |
| `search` | string [≥3 chars] | Búsqueda por nombre |

**Update Frequency:** Cuando se agrega un nuevo país.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /countries
GET /countries?name=england
GET /countries?code=fr
GET /countries?search=engl
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/countries",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"name": "england"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/countries?name=england", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Leagues

Obtiene la lista de ligas y copas disponibles.

```
GET /leagues
```

- Los IDs de liga son únicos y se mantienen entre temporadas.
- Logo URL: `https://media.api-sports.io/football/leagues/{league_id}.png`
- La cobertura (`coverage`) indica qué datos están disponibles para cada liga. Los valores `false` son normales al inicio de temporada y se actualizan cuando la competición comienza.

**Parámetros query (opcionales):**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | integer | ID de la liga |
| `name` | string | Nombre de la liga |
| `country` | string | Nombre del país |
| `code` | string [2-6 chars] | Código Alpha del país |
| `season` | integer (YYYY) | Temporada |
| `team` | integer | ID del equipo |
| `type` | string | `league` o `cup` |
| `current` | string | `true` / `false` — ligas activas |
| `search` | string [≥3 chars] | Búsqueda por nombre o país |
| `last` | integer [≤2 chars] | Últimas X ligas añadidas |

**Update Frequency:** Varias veces al día.  
**Llamadas recomendadas:** 1 vez por hora.

**Casos de uso:**
```
GET /leagues?id=39
GET /leagues?name=premier league
GET /leagues?country=england
GET /leagues?season=2019
GET /leagues?season=2019&id=39
GET /leagues?team=33
GET /leagues?search=premier league
GET /leagues?type=league
GET /leagues?current=true
GET /leagues?last=99
GET /leagues?season=2019&country=england&type=league
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/leagues",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"id": 39, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/leagues?id=39&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Seasons

Obtiene la lista de temporadas disponibles.

```
GET /leagues/seasons
```

- Todas las temporadas son claves de 4 dígitos (YYYY). La temporada 2018-2019 de la Premier League se representa como `2018`.
- Todas las temporadas pueden usarse como filtros en otros endpoints.

**Parámetros:** Ninguno requerido.

**Update Frequency:** Cuando se añade una nueva liga.  
**Llamadas recomendadas:** 1 vez al día.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/leagues/seasons",
    headers={"x-apisports-key": "YOUR_API_KEY"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/leagues/seasons", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Teams

Obtiene la lista de equipos disponibles.

```
GET /teams
```

- Los IDs de equipo son únicos entre todas las ligas.
- Logo URL: `https://media.api-sports.io/football/teams/{team_id}.png`
- **Requiere al menos un parámetro.**

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | integer | ID del equipo |
| `name` | string | Nombre del equipo |
| `league` | integer | ID de la liga |
| `season` | integer (YYYY) | Temporada |
| `country` | string | País del equipo |
| `code` | string [3 chars] | Código del equipo |
| `venue` | integer | ID del estadio |
| `search` | string [≥3 chars] | Búsqueda por nombre o país |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /teams?id=33
GET /teams?name=manchester united
GET /teams?league=39&season=2019
GET /teams?country=england
GET /teams?code=FRA
GET /teams?venue=789
GET /teams?search=manches
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/teams",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/teams?league=39&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Teams Statistics

Estadísticas de un equipo en una competición y temporada específica.

```
GET /teams/statistics
```

Con el parámetro `date` puedes calcular estadísticas desde el inicio de la temporada hasta esa fecha.

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `league` | integer | ✅ | ID de la liga |
| `season` | integer (YYYY) | ✅ | Temporada |
| `team` | integer | ✅ | ID del equipo |
| `date` | string (YYYY-MM-DD) | — | Fecha límite |

**Update Frequency:** 2 veces al día.  
**Llamadas recomendadas:** 1 al día (o 1 a la semana si el equipo no juega ese día).

**Casos de uso:**
```
GET /teams/statistics?league=39&team=33&season=2019
GET /teams/statistics?league=39&team=33&season=2019&date=2019-10-08
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/teams/statistics",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "team": 33, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/teams/statistics?league=39&team=33&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Teams Seasons

Obtiene las temporadas disponibles para un equipo.

```
GET /teams/seasons
```

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `team` | integer | ✅ | ID del equipo |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/teams/seasons",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"team": 33}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/teams/seasons?team=33", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Teams Countries

Obtiene los países disponibles para el endpoint `teams`.

```
GET /teams/countries
```

**Parámetros:** Ninguno requerido.

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/teams/countries",
    headers={"x-apisports-key": "YOUR_API_KEY"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/teams/countries", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Venues

Obtiene la lista de estadios disponibles.

```
GET /venues
```

- Imagen URL: `https://media.api-sports.io/football/venues/{venue_id}.png`
- **Requiere al menos un parámetro.**

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | integer | ID del estadio |
| `name` | string | Nombre del estadio |
| `city` | string | Ciudad |
| `country` | string | País |
| `search` | string [≥3 chars] | Búsqueda por nombre, ciudad o país |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /venues?id=556
GET /venues?name=Old Trafford
GET /venues?city=manchester
GET /venues?country=england
GET /venues?search=trafford
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/venues",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"name": "Old Trafford"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/venues?name=Old%20Trafford", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Standings

Obtiene la tabla de posiciones de una liga o equipo.

```
GET /standings
```

Algunas competiciones tienen múltiples clasificaciones (fase de grupos, apertura, clausura, etc.).

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `season` | integer (YYYY) | ✅ | Temporada |
| `league` | integer | — | ID de la liga |
| `team` | integer | — | ID del equipo |

**Update Frequency:** Cada hora.  
**Llamadas recomendadas:** 1 por hora (si hay partidos en curso), 1 al día (si no).

**Casos de uso:**
```
GET /standings?league=39&season=2019
GET /standings?league=39&team=33&season=2019
GET /standings?team=33&season=2019
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/standings",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/standings?league=39&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Fixtures / Rounds

Obtiene las jornadas (rounds) de una liga o copa.

```
GET /fixtures/rounds
```

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `league` | integer | ✅ | ID de la liga |
| `season` | integer (YYYY) | ✅ | Temporada |
| `current` | boolean | — | Solo la jornada actual |
| `dates` | boolean | — | Incluir fechas de cada jornada |
| `timezone` | string | — | Zona horaria (ver endpoint Timezone) |

**Update Frequency:** Cada día.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /fixtures/rounds?league=39&season=2019
GET /fixtures/rounds?league=39&season=2019&dates=true
GET /fixtures/rounds?league=39&season=2019&current=true
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/fixtures/rounds",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "season": 2023, "current": "true"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/fixtures/rounds?league=39&season=2023&current=true", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Fixtures

Obtiene partidos por múltiples filtros.

```
GET /fixtures
```

- Puedes añadir `timezone` a cualquier request para recibir horarios en tu zona horaria.
- Los IDs de fixture son únicos y nunca cambian.
- Datos actualizados cada **15 segundos**.
- No todas las competiciones tienen livescore; en ese caso el estado permanece `NS` y se actualiza en las horas posteriores al partido (puede tardar hasta 48 h).

**Estados disponibles:**

| Short | Long | Tipo | Descripción |
|-------|------|------|-------------|
| `TBD` | Time To Be Defined | Scheduled | Fecha/hora aún no definida |
| `NS` | Not Started | Scheduled | Aún no comenzó |
| `1H` | First Half | In Play | Primera parte en juego |
| `HT` | Halftime | In Play | Descanso |
| `2H` | Second Half | In Play | Segunda parte en juego |
| `ET` | Extra Time | In Play | Tiempo extra |
| `BT` | Break Time | In Play | Pausa en tiempo extra |
| `P` | Penalty In Progress | In Play | Penales en curso |
| `SUSP` | Match Suspended | In Play | Suspendido por el árbitro |
| `INT` | Match Interrupted | In Play | Interrumpido, se reanuda en minutos |
| `FT` | Match Finished | Finished | Finalizado en tiempo regular |
| `AET` | Match Finished | Finished | Finalizado en tiempo extra |
| `PEN` | Match Finished | Finished | Finalizado en penales |
| `PST` | Match Postponed | Postponed | Pospuesto |
| `CANC` | Match Cancelled | Cancelled | Cancelado |
| `ABD` | Match Abandoned | Abandoned | Abandonado |
| `AWD` | Technical Loss | Not Played | — |
| `WO` | WalkOver | Not Played | Victoria por incomparecencia |
| `LIVE` | In Progress | In Play | Caso muy raro |

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | integer | ID del fixture |
| `ids` | string | Varios IDs separados por `-` (máx. 20) |
| `live` | string | `all` o `id-id` (IDs de ligas) |
| `date` | string (YYYY-MM-DD) | Fecha específica |
| `league` | integer | ID de la liga |
| `season` | integer (YYYY) | Temporada |
| `team` | integer | ID del equipo |
| `last` | integer [≤2 chars] | Últimos X fixtures |
| `next` | integer [≤2 chars] | Próximos X fixtures |
| `from` | string (YYYY-MM-DD) | Desde fecha |
| `to` | string (YYYY-MM-DD) | Hasta fecha |
| `round` | string | Jornada |
| `status` | string | Estado(s) del fixture |
| `venue` | integer | ID del estadio |
| `timezone` | string | Zona horaria |

**Update Frequency:** Cada 15 segundos.  
**Llamadas recomendadas:** 1 por minuto (con partidos en curso), 1 al día (sin partidos).

**Casos de uso:**
```
GET /fixtures?id=215662
GET /fixtures?ids=215662-215663-215664
GET /fixtures?live=all
GET /fixtures?live=39-61-48
GET /fixtures?league=39&season=2019
GET /fixtures?date=2019-10-22
GET /fixtures?next=15
GET /fixtures?last=15
GET /fixtures?league=61&last=10&status=ft
GET /fixtures?team=85&season=2019&from=2019-07-01&to=2020-10-31
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/fixtures",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"live": "all"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/fixtures?live=all", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Head to Head

Obtiene el historial de enfrentamientos entre dos equipos.

```
GET /fixtures/headtohead
```

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `h2h` | string (ID-ID) | ✅ | IDs de los dos equipos |
| `date` | string (YYYY-MM-DD) | — | Fecha |
| `league` | integer | — | ID de la liga |
| `season` | integer (YYYY) | — | Temporada |
| `last` | integer | — | Últimos X fixtures |
| `next` | integer | — | Próximos X fixtures |
| `from` | string (YYYY-MM-DD) | — | Desde fecha |
| `to` | string (YYYY-MM-DD) | — | Hasta fecha |
| `status` | string | — | Estado(s) del fixture |
| `venue` | integer | — | ID del estadio |
| `timezone` | string | — | Zona horaria |

**Update Frequency:** Cada 15 segundos.  
**Llamadas recomendadas:** 1 por minuto (con partidos en curso), 1 al día (sin partidos).

**Casos de uso:**
```
GET /fixtures/headtohead?h2h=33-34
GET /fixtures/headtohead?h2h=33-34&status=ns
GET /fixtures/headtohead?h2h=33-34&from=2019-10-01&to=2019-10-31
GET /fixtures/headtohead?league=39&season=2019&h2h=33-34&last=5
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/fixtures/headtohead",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"h2h": "33-34", "last": 5}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/fixtures/headtohead?h2h=33-34&last=5", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Fixtures Statistics

Estadísticas de un fixture.

```
GET /fixtures/statistics
```

**Estadísticas disponibles:** Shots on/off Goal, Shots inside/outside box, Total Shots, Blocked Shots, Fouls, Corner Kicks, Offsides, Ball Possession, Yellow/Red Cards, Goalkeeper Saves, Total/Accurate Passes, Passes %.

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `fixture` | integer | ✅ | ID del fixture |
| `team` | integer | — | ID del equipo |
| `type` | string | — | Tipo de estadística |
| `half` | boolean | — | Incluir estadísticas por mitad (desde temporada 2024) |

**Update Frequency:** Cada minuto.  
**Llamadas recomendadas:** 1 por minuto (en curso), 1 al día (finalizado).

**Casos de uso:**
```
GET /fixtures/statistics?fixture=215662
GET /fixtures/statistics?fixture=215662&half=true
GET /fixtures/statistics?fixture=215662&type=Total Shots
GET /fixtures/statistics?fixture=215662&team=463
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/fixtures/statistics",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 215662}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/fixtures/statistics?fixture=215662", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Fixtures Events

Obtiene los eventos de un fixture (goles, tarjetas, sustituciones, VAR).

```
GET /fixtures/events
```

**Tipos de evento disponibles:**

| Tipo | Subtipos |
|------|----------|
| `Goal` | Normal Goal, Own Goal, Penalty |
| `Card` | Yellow Card, Red Card |
| `Subst` | Substitution [1, 2, 3…] |
| `Var` | Goal cancelled, Penalty confirmed, Missed Penalty |

> Los eventos VAR están disponibles desde la temporada 2020-2021.

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `fixture` | integer | ✅ | ID del fixture |
| `team` | integer | — | ID del equipo |
| `player` | integer | — | ID del jugador |
| `type` | string | — | Tipo de evento |

**Update Frequency:** Cada 15 segundos.  
**Llamadas recomendadas:** 1 por minuto (en curso), 1 al día (finalizado).

**Casos de uso:**
```
GET /fixtures/events?fixture=215662
GET /fixtures/events?fixture=215662&team=463
GET /fixtures/events?fixture=215662&player=35845
GET /fixtures/events?fixture=215662&type=card
GET /fixtures/events?fixture=215662&team=463&type=goal&player=35845
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/fixtures/events",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 215662, "type": "goal"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/fixtures/events?fixture=215662&type=goal", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Fixtures Lineups

Obtiene las alineaciones de un fixture.

```
GET /fixtures/lineups
```

- Las alineaciones están disponibles entre 20 y 40 minutos antes del partido (si la competición lo soporta).
- Si no están disponibles antes, se publican después del partido con un retardo variable.

**Datos disponibles:** Formación, Entrenador, XI Inicial, Suplentes, Posición en el campo (X:Y).

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `fixture` | integer | ✅ | ID del fixture |
| `team` | integer | — | ID del equipo |
| `player` | integer | — | ID del jugador |
| `type` | string | — | Tipo (`startXI`, `substitutes`) |

**Update Frequency:** Cada 15 minutos.  
**Llamadas recomendadas:** 1 cada 15 minutos (en curso), 1 al día (finalizado).

**Casos de uso:**
```
GET /fixtures/lineups?fixture=592872
GET /fixtures/lineups?fixture=592872&team=50
GET /fixtures/lineups?fixture=215662&player=35845
GET /fixtures/lineups?fixture=215662&type=startXI
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/fixtures/lineups",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 592872}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/fixtures/lineups?fixture=592872", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Fixtures Players

Estadísticas de jugadores de un fixture.

```
GET /fixtures/players
```

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `fixture` | integer | ✅ | ID del fixture |
| `team` | integer | — | ID del equipo |

**Update Frequency:** Cada minuto.  
**Llamadas recomendadas:** 1 por minuto (en curso), 1 al día (finalizado).

**Casos de uso:**
```
GET /fixtures/players?fixture=169080
GET /fixtures/players?fixture=169080&team=2284
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/fixtures/players",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 169080}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/fixtures/players?fixture=169080", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Injuries

Lista de jugadores que no participarán en un fixture (lesionados, suspendidos).

```
GET /injuries
```

> Datos disponibles a partir de **abril 2021**.

**Tipos:** `Missing Fixture` (no jugará) / `Questionable` (posible duda).

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `league` | integer | ID de la liga |
| `season` | integer (YYYY) | Temporada (requerida con league, team y player) |
| `fixture` | integer | ID del fixture |
| `team` | integer | ID del equipo |
| `player` | integer | ID del jugador |
| `date` | string (YYYY-MM-DD) | Fecha |
| `ids` | string | Varios fixture IDs separados por `-` (máx. 20) |
| `timezone` | string | Zona horaria |

**Update Frequency:** Cada 4 horas.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /injuries?league=2&season=2020
GET /injuries?fixture=686314
GET /injuries?ids=686314-686315-686316
GET /injuries?team=85&season=2020
GET /injuries?player=865&season=2020
GET /injuries?date=2021-04-07
GET /injuries?league=2&season=2020&team=85
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/injuries",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 686314}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/injuries?fixture=686314", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Predictions

Predicciones para un fixture basadas en algoritmos estadísticos (distribución de Poisson, estadísticas de equipos, últimos partidos, jugadores). **No se usan cuotas de casas de apuestas.**

```
GET /predictions
```

**Predicciones disponibles:**
- **Match winner:** ID del equipo con mayor probabilidad de ganar
- **Win or Draw:** Si el equipo designado puede ganar o empatar
- **Under/Over:** -1.5 / -2.5 / -3.5 / -4.5 / +1.5 / +2.5 / +3.5 / +4.5
- **Goals Home/Away:** -1.5 / -2.5 / -3.5 / -4.5
- **Advice:** Recomendación narrativa

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `fixture` | integer | ✅ | ID del fixture |

**Update Frequency:** Cada hora.  
**Llamadas recomendadas:** 1 por hora (en curso), 1 al día.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/predictions",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 198772}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/predictions?fixture=198772", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Coachs

Información completa sobre entrenadores y sus carreras.

```
GET /coachs
```

Foto URL: `https://media.api-sports.io/football/coachs/{coach_id}.png`

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | integer | ID del entrenador |
| `team` | integer | ID del equipo |
| `search` | string [≥3 chars] | Nombre del entrenador |

**Update Frequency:** Cada día.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /coachs?id=1
GET /coachs?team=33
GET /coachs?search=Klopp
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/coachs",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"team": 33}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/coachs?team=33", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Players Seasons

Temporadas disponibles para estadísticas de jugadores.

```
GET /players/seasons
```

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `player` | integer | ID del jugador (opcional) |

**Update Frequency:** Cada día.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /players/seasons
GET /players/seasons?player=276
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/seasons",
    headers={"x-apisports-key": "YOUR_API_KEY"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/seasons", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Players Profiles

Retorna la lista de todos los jugadores disponibles.

```
GET /players/profiles
```

- Foto URL: `https://media.api-sports.io/football/players/{player_id}.png`
- **Paginación:** 250 resultados por página.

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `player` | integer | ID del jugador |
| `search` | string [≥3 chars] | Apellido del jugador |
| `page` | integer | Número de página (default: 1) |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez a la semana.

**Casos de uso:**
```
GET /players/profiles?player=276
GET /players/profiles?search=ney
GET /players/profiles
GET /players/profiles?page=2
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/profiles",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"search": "messi"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/profiles?search=messi", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Players Statistics

Estadísticas de jugadores por temporada.

```
GET /players
```

- Las estadísticas se calculan según `team`, `league` y `season`.
- Un jugador puede tener estadísticas para 2 equipos en la misma temporada (transferencias).
- Los IDs de jugador son únicos y permanentes.
- **Paginación:** 20 resultados por página.

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | integer | ID del jugador |
| `team` | integer | ID del equipo |
| `league` | integer | ID de la liga |
| `season` | integer (YYYY) | Temporada (requerida con id, league o team) |
| `search` | string [≥4 chars] | Nombre del jugador (requiere league o team) |
| `page` | integer | Número de página (default: 1) |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /players?id=19088&season=2018
GET /players?season=2018&team=33
GET /players?season=2018&league=61
GET /players?season=2018&league=61&team=33
GET /players?team=85&search=cavani
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"season": 2023, "team": 33}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players?season=2023&team=33", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Players Squads

Retorna el plantel actual de un equipo, o los equipos donde ha jugado un jugador.

```
GET /players/squads
```

**Parámetros query (al menos uno requerido):**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `team` | integer | ID del equipo |
| `player` | integer | ID del jugador |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez a la semana.

**Casos de uso:**
```
GET /players/squads?team=33
GET /players/squads?player=276
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/squads",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"team": 33}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/squads?team=33", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Players Teams

Retorna los equipos y temporadas en las que un jugador participó durante su carrera.

```
GET /players/teams
```

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `player` | integer | ✅ | ID del jugador |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez a la semana.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/teams",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"player": 276}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/teams?player=276", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Top Scorers

Top 20 goleadores de una liga o copa.

```
GET /players/topscorers
```

**Criterios de desempate (en orden):**
1. Mayor número de goles
2. Menor número de penales convertidos
3. Mayor número de asistencias
4. Goles en más partidos distintos
5. Menor cantidad de minutos jugados
6. Equipo mejor posicionado en la tabla
7. Menor número de tarjetas rojas
8. Menor número de tarjetas amarillas

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `league` | integer | ✅ | ID de la liga |
| `season` | integer (YYYY) | ✅ | Temporada |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/topscorers",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/topscorers?league=39&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Top Assists

Top 20 asistidores de una liga o copa.

```
GET /players/topassists
```

**Criterios de desempate (en orden):**
1. Mayor número de asistencias
2. Mayor número de goles
3. Menor número de penales convertidos
4. Asistencias en más partidos distintos
5. Menor cantidad de minutos jugados
6. Menor número de tarjetas rojas
7. Menor número de tarjetas amarillas

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `league` | integer | ✅ | ID de la liga |
| `season` | integer (YYYY) | ✅ | Temporada |

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/topassists",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/topassists?league=39&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Top Yellow Cards

Top 20 jugadores con más tarjetas amarillas.

```
GET /players/topyellowcards
```

**Criterios de desempate:** Mayor tarjetas amarillas → Mayor tarjetas rojas → Más partidos → Menos minutos.

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `league` | integer | ✅ | ID de la liga |
| `season` | integer (YYYY) | ✅ | Temporada |

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/topyellowcards",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/topyellowcards?league=39&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Top Red Cards

Top 20 jugadores con más tarjetas rojas.

```
GET /players/topredcards
```

**Criterios de desempate:** Mayor tarjetas rojas → Mayor tarjetas amarillas → Más partidos → Menos minutos.

**Parámetros query:**

| Parámetro | Tipo | Obligatorio | Descripción |
|-----------|------|:-----------:|-------------|
| `league` | integer | ✅ | ID de la liga |
| `season` | integer (YYYY) | ✅ | Temporada |

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/players/topredcards",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"league": 39, "season": 2023}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/players/topredcards?league=39&season=2023", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Transfers

Todos los traspasos disponibles para jugadores y equipos.

```
GET /transfers
```

**Parámetros query (al menos uno requerido):**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `player` | integer | ID del jugador |
| `team` | integer | ID del equipo |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /transfers?player=35845
GET /transfers?team=463
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/transfers",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"player": 35845}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/transfers?player=35845", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Trophies

Todos los trofeos disponibles para un jugador o entrenador.

```
GET /trophies
```

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `player` | integer | ID del jugador |
| `players` | string | Varios IDs separados por `-` (máx. 20) |
| `coach` | integer | ID del entrenador |
| `coachs` | string | Varios IDs separados por `-` (máx. 20) |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /trophies?player=276
GET /trophies?players=276-278
GET /trophies?coach=2
GET /trophies?coachs=2-6
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/trophies",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"player": 276}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/trophies?player=276", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Sidelined

Historial de lesiones/ausencias para un jugador o entrenador.

```
GET /sidelined
```

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `player` | integer | ID del jugador |
| `players` | string | Varios IDs separados por `-` (máx. 20) |
| `coach` | integer | ID del entrenador |
| `coachs` | string | Varios IDs separados por `-` (máx. 20) |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /sidelined?player=276
GET /sidelined?players=276-278-279
GET /sidelined?coach=2
GET /sidelined?coachs=2-6-44
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/sidelined",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"player": 276}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/sidelined?player=276", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Odds (In-Play)

Cuotas en vivo para fixtures en progreso.

```
GET /odds/live
```

- Los fixtures se añaden entre 15 y 5 minutos antes del inicio, y se eliminan entre 5 y 20 minutos después de finalizar.
- **No se almacena historial.**

**Update Frequency:** Cada 5 segundos (puede variar entre 5 y 60 s).

**Campo `main`:** Cuando existen varios valores idénticos para la misma apuesta, `main: true` indica cuál considerar.

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `fixture` | integer | ID del fixture |
| `league` | integer | ID de la liga |
| `bet` | integer | ID de la apuesta |

**Casos de uso:**
```
GET /odds/live
GET /odds/live?fixture=164327
GET /odds/live?league=39
GET /odds/live?bet=4&league=39
GET /odds/live?bet=4&fixture=164327
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/odds/live",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 164327}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/odds/live?fixture=164327", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

#### odds/live/bets

Obtiene todas las apuestas disponibles para cuotas en vivo.

```
GET /odds/live/bets
```

> Los IDs de este endpoint **no son compatibles** con el endpoint `odds` (pre-partido).

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | string | ID del tipo de apuesta |
| `search` | string [3 chars] | Nombre de la apuesta |

**Casos de uso:**
```
GET /odds/live/bets
GET /odds/live/bets?id=1
GET /odds/live/bets?search=winner
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/odds/live/bets",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"search": "winner"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/odds/live/bets?search=winner", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Odds (Pre-Match)

Cuotas pre-partido de fixtures, ligas o fechas.

```
GET /odds
```

- Disponibles entre **1 y 14 días antes** del fixture.
- Historial de **7 días** (disponibilidad variable según liga, temporada y bookmaker).
- **Paginación:** 10 resultados por página.

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `fixture` | integer | ID del fixture |
| `league` | integer | ID de la liga |
| `season` | integer (YYYY) | Temporada |
| `date` | string (YYYY-MM-DD) | Fecha |
| `timezone` | string | Zona horaria |
| `page` | integer | Página (default: 1) |
| `bookmaker` | integer | ID del bookmaker |
| `bet` | integer | ID de la apuesta |

**Update Frequency:** Cada 3 horas.  
**Llamadas recomendadas:** 1 cada 3 horas.

**Casos de uso:**
```
GET /odds?fixture=164327
GET /odds?league=39&season=2019
GET /odds?date=2020-05-15
GET /odds?bookmaker=1&bet=4&league=39&season=2019
GET /odds?date=2020-05-15&page=2&bet=4
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/odds",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"fixture": 164327, "bookmaker": 6}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/odds?fixture=164327&bookmaker=6", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Odds Mapping

Lista de IDs de fixtures disponibles para el endpoint `odds`.

```
GET /odds/mapping
```

**Paginación:** 100 resultados por página.

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `page` | integer | Página (default: 1) |

**Update Frequency:** Cada día.  
**Llamadas recomendadas:** 1 vez al día.

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/odds/mapping",
    headers={"x-apisports-key": "YOUR_API_KEY"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/odds/mapping", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Bookmakers

Lista de todas las casas de apuestas disponibles.

```
GET /odds/bookmakers
```

Los IDs de bookmaker pueden usarse como filtro en el endpoint `odds`.

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | integer | ID del bookmaker |
| `search` | string [3 chars] | Nombre del bookmaker |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /odds/bookmakers
GET /odds/bookmakers?id=1
GET /odds/bookmakers?search=Betfair
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/odds/bookmakers",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"search": "Betfair"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/odds/bookmakers?search=Betfair", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

### Bets

Lista de todas las apuestas disponibles para cuotas pre-partido.

```
GET /odds/bets
```

> Los IDs de este endpoint **no son compatibles** con el endpoint `odds/live`.

**Parámetros query:**

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `id` | string | ID del tipo de apuesta |
| `search` | string [3 chars] | Nombre de la apuesta |

**Update Frequency:** Varias veces a la semana.  
**Llamadas recomendadas:** 1 vez al día.

**Casos de uso:**
```
GET /odds/bets
GET /odds/bets?id=1
GET /odds/bets?search=winner
```

**Python:**
```python
import requests

response = requests.get(
    "https://v3.football.api-sports.io/odds/bets",
    headers={"x-apisports-key": "YOUR_API_KEY"},
    params={"search": "winner"}
)
print(response.json())
```

**JavaScript:**
```javascript
fetch("https://v3.football.api-sports.io/odds/bets?search=winner", {
  method: "GET",
  headers: { "x-apisports-key": "YOUR_API_KEY" }
})
  .then(res => res.json())
  .then(data => console.log(data));
```

---

## Changelog

### v3.9.3
- **`/players/profiles`** — Nuevo endpoint con lista de todos los jugadores disponibles.
- **`/players/teams`** — Nuevo endpoint con equipos y temporadas de la carrera de un jugador.
- **`/fixtures`** — Nuevo campo `extra` (tiempo adicional jugado en cada mitad); nuevo campo `standings` (indica si la competición tiene tabla).
- **`/fixtures/rounds`** — Nuevo parámetro `half` para estadísticas de primera y segunda mitad; nuevo parámetro `dates`.
- **`/fixtures/statistics`** — Nuevo parámetro `half` para estadísticas por mitad.
- **`/teams/statistics`** — Nuevas estadísticas: Goals Over, Goals Under.
- **`/sidelined`** — Nuevos parámetros `players` y `coachs` (múltiples IDs).
- **`/injuries`** — Nuevo parámetro `coachs` para múltiples entrenadores.
- **`/trophies`** — Nuevos parámetros `players` y `coachs` (múltiples IDs).

### v3.9.2
- **`/odds/live`** — Nuevo endpoint de cuotas en vivo.
- **`/odds/live/bets`** — Nuevo endpoint de apuestas para cuotas en vivo.
- **`/teams`** — Nuevos parámetros: `code`, `venue`; nuevo endpoint `/teams/countries`.
- **`/fixtures`** — Parámetro `ids` (múltiples fixtures); múltiples estados en `status`; nuevo parámetro `venue`.
- **`/fixtures/headtohead`** — Múltiples estados en `status`; nuevo parámetro `venue`.

### v3.8.1
- Nuevos endpoints: `/injuries`, `/players/squads`, `/players/topassists`, `/players/topyellowcards`, `/players/topredcards`.
- **`/fixtures/lineups`** — Posiciones en el campo (grid) y colores de camiseta de jugadores.
- **`/fixtures/events`** — Eventos VAR.
- **`/teams`** — Código tricolor (tri-code).
- **`/teams/statistics`** — Minuto de gol, tarjetas por minuto, formación más usada, estadísticas de penales.
- Fotos de entrenadores disponibles.

---

*Documentación generada y optimizada desde API-Football v3.9.3 — Solo ejemplos en Python y JavaScript.*
