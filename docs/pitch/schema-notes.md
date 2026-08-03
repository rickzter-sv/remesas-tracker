# Schema notes — export_dashboard_data.py vs. schema real

Verificado el 2026-08-03 corriendo `sqlite3 remesas.db ".schema"` (raíz del
repo, no `db/`) y comparando contra las queries originales del script
generado en la sesión de Claude Chat. Todas las correcciones ya están
aplicadas en `export_dashboard_data.py`.

## 0. Ruta de la base de datos

- Asumida: `db/remesas.db`
- Real: `remesas.db` (raíz del repo)
- El script no habría encontrado la base en ningún entorno local ni en CI.

## 1. Tabla `operators`

| Columna asumida | Columna real | Nota |
|---|---|---|
| `id` | `operator_id` | PK real se llama `operator_id`; se alias a `id` en el SELECT para no romper el contrato JSON que espera `docs/index.html`. |

`name` y `requires_manual_sampling` coinciden.

## 2. Tabla `corridors`

| Columna asumida | Columna real |
|---|---|
| `id` | `corridor_id` (aliased a `id`) |
| `currency_origin` | `origin_currency` |
| `currency_destination` | `destination_currency` |

## 3. Tabla `observations`

| Columna/campo asumido | Real | Nota |
|---|---|---|
| `o.id` | `o.observation_id` | aliased a `id` |
| `o.commission_fee` | `o.fee` | aliased a `commission_fee` para mantener el contrato JSON |
| `o.observed_at` | `o.timestamp_utc` | usado también en el WHERE y ORDER BY, no solo en el SELECT |
| join `op.id = o.operator_id` | `op.operator_id = o.operator_id` | `operators` no tiene columna `id` |
| join `c.id = o.corridor_id` | `c.corridor_id = o.corridor_id` | `corridors` no tiene columna `id` |

## 4. Tabla `evidence`

| Columna/campo asumido | Real | Nota |
|---|---|---|
| `evidence.operator_id` | **no existe** | `evidence` solo tiene `observation_id`. Para agrupar evidencia por operador hace falta un JOIN con `observations` (`evidence.observation_id = observations.observation_id`) y usar `observations.operator_id`. Ya corregido en `fetch_evidence_counts()`. |
| `created_at` | `captured_at` | |

## 5. Columnas que sí coinciden sin cambios

`send_amount`, `total_cost_pct`, `is_promotional`, `delivery_method` — nombres idénticos en real y supuesto.

## 6. Hallazgo importante — `fx_margin_pct` no existe como dato real

El script original (y el HTML del dashboard) asumen un campo `fx_margin_pct`
por observación. **No existe ninguna columna equivalente en `observations`**
— el schema real tiene `exchange_rate_applied` y `receive_amount`, pero no
un margen cambiario ya calculado.

Peor aún: revisé cómo cada colector calcula `total_cost_pct`
(`collectors/ria.py:243`, `remitbee.py:251`, `xoom.py:243`,
`manual_entry.py:154`, `remitly.py:300` y `:383`, `western_union.py:179` y
`:310`) y en **todos** los casos la fórmula real es:

```
total_cost_pct = fee / send_amount * 100
```

Es decir, `total_cost_pct` **nunca incorpora el margen cambiario**, a pesar
de que `docs/methodology.md:25` documenta la metodología pretendida como:

> costo total = comisión fija + margen implícito en el tipo de cambio

Esto es una discrepancia entre metodología documentada e implementación
real de los colectores, no un simple error de nombre de columna. No la
resolví — no me corresponde decidir si:

(a) se corrige `methodology.md` para reflejar que hoy solo se mide comisión
    (el corredor US→SV no tiene conversión de moneda, así que ahí no importa;
    pero el corredor CA→SV sí implica FX y hoy ese margen no se está
    capturando en el costo total), o
(b) se actualizan los colectores para calcular el margen cambiario real
    (requeriría una tasa de referencia/mid-market para comparar contra
    `exchange_rate_applied`) y sumarlo a `total_cost_pct`.

**Decisión aplicada por defecto en el export (mínimo cambio, no fabrica
datos):** `fx_margin_pct` se exporta como `0.0` fijo en todas las filas —
así el dashboard no muestra un número inventado, pero para el corredor
CA→SV el "Costo promedio" mostrado en el dashboard queda subestimado
respecto al margen cambiario real hasta que (a) o (b) se decida.
Pendiente de tu confirmación.

## 7. Implementación 2026-08-03 — `evidence.file_status` y `observations.run_type`

Sigue al diagnóstico de la sesión anterior (evidencia faltante de Ria/Xoom/WU/
RemitBee/Remitly — causas: purga deliberada de corridas de prueba vía
`prune_evidence.py --since`, `git filter-repo` sobre blobs de Xoom >500KB, y
un lote de Ria/Xoom que nunca se comiteó por ser pre-producción). Decisiones
ya tomadas por el usuario, aplicadas tal cual:

### 7.1 `evidence.file_status` (`'available'` | `'file_missing'`)

Columna nueva, `NOT NULL DEFAULT 'available'`. Backfill basado en existencia
real en disco (no en patrones de nombre ni fechas — cada una de las 59 rutas
distintas de `evidence` se verificó con `Path.exists()`):

- **40 rutas distintas confirmadas faltantes → 221 filas de `evidence`
  marcadas `file_missing`**: Western Union (30 filas / 6 archivos), RemitBee
  (12 filas / 3 archivos), Remitly (24 filas / 2 archivos), Xoom lote
  post-producción del 08-03T02:15 UTC (69 filas / 6 archivos — el que el
  usuario pidió explícitamente marcar), **más** Ria (17 filas / 17 archivos)
  y el lote pre-producción de Xoom del 08-02T03:44 UTC (69 filas / 6
  archivos). Estos dos últimos grupos no estaban en la lista explícita del
  usuario para este punto, pero sus archivos también están confirmados
  ausentes — dejarlos en `'available'` habría sido incorrecto, así que los
  incluí aquí también. Quedan además excluidos del export por el punto 7.2.
- **120 filas (19 archivos reales, verificados en disco) → `'available'`.**

### 7.2 `observations.run_type` (`'scheduled'` | `'manual_test'` | `'pre_production_test'`)

Columna nueva, `NOT NULL DEFAULT 'manual_test'` (default conservador: un
colector que olvide setearlo queda protegido/excluido, nunca lo contrario).

Backfill histórico:
- **86 observaciones → `'pre_production_test'`**: las 17 de Ria (100% de sus
  observaciones — nunca hubo una corrida de Ria después de que
  `f95d5c9` activara la automatización) + las 69 de Xoom con
  `timestamp_utc < 2026-08-02T07:16:42Z` (hora exacta del commit `f95d5c9`).
  Confirmado con `git show f95d5c9 --stat`: ese commit sube evidencia de WU/
  RemitBee/Remitly pero **cero** archivos de Ria/Xoom, aunque agrega
  `ria.py`/`xoom.py` — consistente con que ese primer lote fue una corrida
  local de prueba antes de comitear el código.
- **255 observaciones → `'scheduled'`**: todo lo demás (incluye Xoom
  post-producción, y las observaciones de las corridas de
  `workflow_dispatch` del 08-03T02:15 y 08-03T04:23 UTC). No se
  retro-clasificaron como `manual_test` porque no quedó registrado en su
  momento qué disparó cada corrida histórica (`GITHUB_EVENT_NAME` no se
  guardaba); el usuario solo pidió excluir explícitamente el lote
  pre-producción de Ria/Xoom, no estas otras corridas de prueba conocidas
  (schedule accidental 08-02T10:50, workflow_dispatch 08-03T02:15 y
  08-03T04:23) — esas quedan con evidencia `file_missing` donde aplica
  (punto 7.1) pero sus observaciones siguen públicas.

### 7.3 Hacia adelante

`collectors/utils.py` agrega `get_run_type()`: lee `GITHUB_EVENT_NAME`, mapea
`'schedule'` → `'scheduled'`, cualquier otro valor (incluido no-seteado, ej.
corridas locales) → `'manual_test'`. Los 6 colectores automáticos (WU x2
corredores, Ria, Xoom, RemitBee, Remitly) ahora llaman esto para poblar
`run_type` en cada observación nueva.

`collectors/manual_entry.py` (muestreo manual de MoneyGram) es un caso
especial: corre localmente, nunca dentro de GitHub Actions, así que
`GITHUB_EVENT_NAME` nunca está seteado — usar `get_run_type()` ahí lo
clasificaría siempre como `'manual_test'` y lo excluiría del export público,
a pesar de ser el método de recolección de producción legítimo para
MoneyGram (`requires_manual_sampling=1`). Por eso `manual_entry.py` setea
`run_type='scheduled'` de forma explícita y hardcodeada, no vía
`get_run_type()`. Documentado en el propio código.

`export_dashboard_data.py` ahora filtra `WHERE o.run_type = 'scheduled'` en
`fetch_observations()` — esto excluye automáticamente tanto el lote
pre-producción (punto 7.2) como cualquier `manual_test` futuro.

`scripts/prune_evidence.py` ahora se niega (falla con error explícito, no
purga parcial en silencio) a borrar cualquier archivo referenciado por al
menos una observación con `run_type='scheduled'`, en el modo de purga por
`--since`/`--days` (el modo `--include-orphans` no necesita este chequeo:
por definición un huérfano no tiene ninguna fila de `evidence` que lo ligue
a una observación).

### 7.4 `remitly.py` — mismo fix de dedup que 9df7e3a, aplicado hoy

`9df7e3a` (2026-08-02) aplicó "chequear dedup antes de capturar evidencia" a
WU/Ria/Xoom/RemitBee, pero **no** a `remitly.py` — confirmado por el propio
`git show 9df7e3a --stat`, que no toca ese archivo. La corrida de las 12:35
UTC del 08-03 lo confirmó en producción: generó 2 archivos de evidencia
huérfanos (`remitly_us/ca_20260803T123543Z.html`, 0 filas en `evidence`,
señalados en el resumen anterior).

Aplicado el mismo patrón hoy, adaptado a la forma de trabajar de Remitly (fetch
HTTP + parseo de tabla, no Playwright): a diferencia de los otros 4
colectores, los métodos de entrega de Remitly no son un mapa estático conocido
de antemano, salen de las filas de la tabla de tarifas ya descargada. Por eso
el `fetch_html()` + `parse_fee_table()` siguen ocurriendo siempre (no tienen
costo ni escriben a disco), pero `save_evidence()` (el guardado real del
archivo) ahora ocurre **después** de `any_pending_today()` — si todo el
corredor ya está recolectado hoy, se omite sin guardar nada. Verificado
corriendo `python collectors/remitly.py` contra la base real: detectó
"todo ya recolectado hoy" para ambos corredores y no creó ningún archivo
nuevo en `evidence/` (antes del fix, siempre guardaba el HTML sin importar
el dedup).

## 8. Seguimiento pendiente (NO implementado — solo anotado para decidir después)

- **Ria: 3 fallos consecutivos del mismo selector.** Reproducido de forma
  idéntica en las corridas de 2026-08-03T02:15, 04:23 y 12:35 UTC —
  `playwright._impl._errors.TimeoutError: Locator.click: Timeout 10000ms
  exceeded` esperando `get_by_role("option").first` en el corredor US->SV.
  Consistente y reproducible, no una falla intermitente — sigue apuntando a
  que el sitio cambió algo en su dropdown, no a que sea un problema de red o
  timing. `requires_manual_sampling=1` ya aplicado (ver operators.notes,
  operator_id=3); si el usuario quiere recuperar la automatización, este es
  el punto de partida para depurar el selector.
- **Xoom: fallo nuevo, primera vez, distinto al de Ria.** En la corrida de
  2026-08-03T12:35 UTC, Xoom falló por primera vez con
  `TimeoutError: Locator.click: Timeout 10000ms exceeded` esperando
  `get_by_role("button", name="Show Fees")` — no pudo ni abrir el panel de
  tarifas. No se sabe todavía si es intermitente o el inicio de un patrón
  como el de Ria; no se investigó a fondo. Vale la pena revisar la próxima
  corrida antes de decidir si necesita el mismo tratamiento que Ria.
- **Cron disparado 3h20min tarde.** La corrida programada para las 09:15 UTC
  del 2026-08-03 en realidad arrancó a las 12:34:59 UTC. Pudo ser congestión
  de runners de GitHub Actions en ese momento; no se investigó más. Si esto
  se repite, vale la pena revisar el historial de corridas para ver si es un
  patrón o fue un evento aislado.
- **Decisión pendiente de `fx_margin_pct`** (ver sección 6, sin cambios):
  sigue exportándose como `0.0` fijo. Falta decidir entre corregir
  `docs/methodology.md` para reflejar que hoy solo se mide comisión, o
  actualizar los colectores para calcular el margen cambiario real contra una
  tasa de referencia externa (factible, ver diagnóstico de la sesión
  anterior — ningún colector necesita cambios para capturar la tasa propia,
  ya la capturan todos; falta solo la tasa de referencia y la fórmula).
