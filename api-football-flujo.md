# API-Football — Guía de Lógica de Flujo

> Manual operativo actualizado: ver `api-football-operaciones.md` para la estrategia de ingesta, coverage, catalogos y ahorro de cuota aterrizados en el codigo del repo.

> Complemento del documento `api-football-docs.md`. Orientado a uso práctico en Python/JS.

---

## 1. Orden lógico de consulta

El árbol de dependencias de la API sigue esta jerarquía. Los IDs obtenidos en cada nivel se usan como parámetros en el siguiente.

```
[1] /timezone          → obtener timezone válido para tu región
       │
[2] /countries         → obtener country_name / country_code
       │
[3] /leagues           → obtener league_id  (filtra por country + season)
       │
[4] /leagues/seasons   → confirmar qué seasons existen
       │
    ┌──┴──────────────┬──────────────┐
[5] /teams         /standings    /fixtures/rounds
  (league+season)  (league+season)  (league+season)
       │
    ┌──┴────────────────────────────────────────────┐
[6] /fixtures                                   /venues
  (league+season  ó  team  ó  date  ó  live=all)
       │
    ┌──┼──────────────┬──────────────┬──────────────┐
[7] /fixtures    /fixtures     /fixtures      /fixtures
   /statistics   /events       /lineups       /players
  (fixture_id)  (fixture_id)  (fixture_id)   (fixture_id)
       │
[8] /injuries        /predictions
  (fixture ó team)   (fixture_id)
```

**Flujo mínimo para una app de resultados:**
```
/leagues → /fixtures?live=all → /fixtures/events + /fixtures/statistics
```

**Flujo mínimo para perfil de jugador:**
```
/players/profiles?search= → /players?id=&season= → /trophies + /transfers + /sidelined
```

**Flujo mínimo para predicciones:**
```
/leagues → /fixtures?next=10 → /predictions?fixture= + /odds?fixture=
```

---

## 2. Parámetros obligatorios — Top 5 endpoints

### `/fixtures`
| Parámetro | Cuándo es obligatorio |
|---|---|
| *(ninguno técnicamente)* | Pero siempre incluir al menos uno: |
| `live=all` | Para partidos en curso |
| `league` + `season` | Para fixtures de una competición |
| `team` + `season` | Para fixtures de un equipo |
| `date` | Para fixtures de una fecha |
| `id` o `ids` | Para un fixture específico |

> ⚠️ Sin ningún parámetro la API devuelve error. Se requiere **al menos uno**.

---

### `/players` (estadísticas)
| Parámetro | Obligatorio |
|---|---|
| `season` | ✅ siempre (junto con id, league o team) |
| `id` | Si quieres un jugador específico |
| `league` o `team` | Si buscas por competición/equipo |
| `page` | Necesario para iterar (250 resultados/pág) |

---

### `/standings`
| Parámetro | Obligatorio |
|---|---|
| `season` | ✅ siempre |
| `league` o `team` | Al menos uno |

---

### `/teams/statistics`
| Parámetro | Obligatorio |
|---|---|
| `league` | ✅ siempre |
| `season` | ✅ siempre |
| `team` | ✅ siempre |
| `date` | Opcional — limita stats hasta esa fecha |

---

### `/odds` (pre-match)
| Parámetro | Obligatorio |
|---|---|
| *(ninguno técnico)* | Pero sin filtro devuelve demasiado. Usar: |
| `fixture` | Para odds de un partido específico |
| `league` + `season` | Para odds de una competición |
| `bookmaker` | Filtrar por casa de apuestas |
| `bet` | Filtrar por tipo de apuesta |
| `page` | Necesario (10 resultados/pág) |

---

## 3. Tabla de caché recomendado

| Dato | Endpoint | Frecuencia de update (API) | Caché recomendado |
|---|---|---|---|
| Timezones | `/timezone` | Estático | ♾️ indefinido / arranque de app |
| Países | `/countries` | Muy poco frecuente | 7 días |
| Ligas y temporadas | `/leagues` | Al agregar ligas | 24 horas |
| Temporadas disponibles | `/leagues/seasons` | Al agregar ligas | 24 horas |
| Equipos | `/teams` | Diario | 24 horas |
| Estadísticas de equipo | `/teams/statistics` | Diario | 6 horas |
| Estadios | `/venues` | Muy poco frecuente | 7 días |
| Clasificaciones | `/standings` | Diario | 1–2 horas |
| Jornadas | `/fixtures/rounds` | Diario | 24 horas |
| Fixtures (no en curso) | `/fixtures` | Diario | 1 hora |
| Fixtures (en curso) | `/fixtures?live=all` | Cada 15s | 15–30 segundos |
| Estadísticas de partido | `/fixtures/statistics` | Cada 15s (en vivo) | 15–30s en vivo / 1h post-partido |
| Eventos del partido | `/fixtures/events` | Cada 15s (en vivo) | 15–30s en vivo / 1h post-partido |
| Alineaciones | `/fixtures/lineups` | Cada 15 min | 10 min antes del partido / 24h si finalizado |
| Stats de jugadores (fixture) | `/fixtures/players` | Cada 1 min | 60s en vivo / 1h post-partido |
| Lesiones/bajas | `/injuries` | Diario | 6 horas |
| Predicciones | `/predictions` | Cada hora | 1 hora |
| Coaches | `/coachs` | Diario | 24 horas |
| Estadísticas de jugador | `/players` | Diario | 6 horas |
| Plantillas | `/players/squads` | Varias veces/semana | 24 horas |
| Tops (scorers, assists…) | `/players/top*` | Varias veces/semana | 6 horas |
| Transferencias | `/transfers` | Varias veces/semana | 12 horas |
| Trofeos | `/trophies` | Varias veces/semana | 24 horas |
| Ausencias históricas | `/sidelined` | Varias veces/semana | 24 horas |
| Odds en vivo | `/odds/live` | Cada 5–60s | 5–10 segundos |
| Odds pre-match | `/odds` | Cada 3h | 3 horas |
| Bookmakers / Bets | `/odds/bookmakers`, `/odds/bets` | Varias veces/semana | 24 horas |

---

## 4. Reglas de cuota — errores comunes

| Situación | Problema | Solución |
|---|---|---|
| Se llaman `/leagues` y `/countries` en cada request | Desperdicia cuota en datos estáticos | Cachear al inicio, refrescar 1×/día |
| Se llama `/fixtures?live=all` cada segundo | Supera el rate limit por minuto | Pooling cada 15–30s máximo |
| Se llama `/fixtures/lineups` antes del partido | No hay datos hasta ~60 min antes | Llamar solo desde 90 min antes del kick-off |
| Se llaman logos en cada render | Pueden triggear rate limit de media | Servir desde CDN propio o cachear en base de datos |
| Se usan headers extra (Content-Type, etc.) | La API solo acepta `x-apisports-key` | Eliminar cualquier header adicional |
| `/players` sin `season` | Error de parámetros | `season` siempre requerido con player stats |

---

## 5. Patrón de implementación recomendado

### Inicialización de la app (una vez)
```python
# Cargar y cachear datos base
timezones  = get("/timezone")         # cachear ♾️
countries  = get("/countries")        # cachear 7 días
leagues    = get("/leagues?current=true")  # cachear 24h
```

### Ciclo de partido en vivo
```python
import time

def live_loop(fixture_id):
    while partido_en_curso:
        fixture   = get(f"/fixtures?id={fixture_id}")
        events    = get(f"/fixtures/events?fixture={fixture_id}")
        stats     = get(f"/fixtures/statistics?fixture={fixture_id}")
        time.sleep(15)  # respetar el update de 15s de la API
```

### Ciclo de partido en vivo (JS)
```js
async function liveLoop(fixtureId) {
  const poll = async () => {
    const [fixture, events, stats] = await Promise.all([
      fetch(`/fixtures?id=${fixtureId}`, opts).then(r => r.json()),
      fetch(`/fixtures/events?fixture=${fixtureId}`, opts).then(r => r.json()),
      fetch(`/fixtures/statistics?fixture=${fixtureId}`, opts).then(r => r.json())
    ])
    // procesar datos...
    setTimeout(poll, 15000) // cada 15s
  }
  poll()
}
```

### Verificar cuota antes de hacer requests
```python
def check_quota():
    r = requests.get(BASE + "status", headers=HEADERS)
    data = r.json()["response"]
    remaining = data["requests"]["limit_day"] - data["requests"]["current"]
    if remaining < 50:
        print(f"⚠️ Cuota baja: {remaining} requests restantes hoy")
    return remaining
```

---

## 6. IDs importantes a guardar en base de datos

Para evitar re-consultar endpoints lentos, conviene persistir localmente:

| ID | Obtención | Uso |
|---|---|---|
| `league_id` | `/leagues` | Filtro en fixtures, standings, players, odds |
| `team_id` | `/teams` | Filtro en fixtures, stats, injuries, transfers |
| `fixture_id` | `/fixtures` | Todos los sub-endpoints de fixtures |
| `player_id` | `/players` | Stats, trophies, transfers, sidelined |
| `venue_id` | `/venues` | Filtro en fixtures y teams |
| `bookmaker_id` | `/odds/bookmakers` | Filtro en odds |
| `bet_id` | `/odds/bets` | Filtro en odds pre-match |
| `live_bet_id` | `/odds/live/bets` | Filtro en odds/live |

---

## 7. Notas clave de la API

- Las **temporadas son de 4 dígitos** (YYYY). Para ligas que abarcan dos años como la Premier League, la temporada `2018-2019` se representa como `2018`.
- El `league_id` es **único y permanente** — no cambia entre temporadas.
- El `fixture_id` es **único y nunca cambia**.
- **Paginación:** `/players` devuelve 250 por página; `/odds` devuelve 10; `/odds/mapping` devuelve 100.
- Los **logos no cuentan** contra la cuota diaria pero sí tienen rate limit por segundo/minuto.
- El endpoint `/status` tampoco cuenta contra la cuota — ideal para health checks.
- `odds/live` **no tiene historial** — una vez que el partido termina, los datos desaparecen en 5-20 min.
- `odds/live/bets` y `odds/bets` **no son intercambiables** — sus IDs no son compatibles entre sí.
