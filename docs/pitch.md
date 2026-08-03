# Pitch — El costo real de enviar remesas a El Salvador

> Borrador de pitch. Datos de la serie del BCR ya cargados y la reforma de la Ley Bitcoin ya verificada con fuentes (ver sección Fuentes). Falta tono editorial, título de gancho, y el gráfico antes de mandar.

## Resumen en una línea

El Salvador recibe remesas equivalentes a cerca de un cuarto de su PIB. Sabemos cuánto entra al país (BCR lo publica desde hace años). Lo que nadie documenta con evidencia verificable, día a día, es **cuánto se pierde en el camino** — y ese es el vacío que este proyecto llena.

## Dos piezas, dos velocidades

### Pieza ancla — ya publicable: la serie histórica del BCR (2019-2026)

**Fuente:** Banco Central de Reserva de El Salvador (BDEF), serie "Ingresos de remesas familiares por agente liquidador", datos hasta junio 2026, consultada 2026-07-31 — ver [`data/bcr/bcr-serie-canales.csv`](../data/bcr/bcr-serie-canales.csv) en este repo. Todas las cifras de esta sección salen directo de ese archivo; ninguna está inventada ni estimada.

**El dato ancla: el canal de billeteras digitales de criptomonedas.**

| Año | Monto (millones USD) | % del total de remesas del año | Total remesas del año (millones USD) |
|---|---|---|---|
| 2019 | — | — | 5,656.18 |
| 2020 | — | — | 5,929.93 |
| 2021 | 44.55 | 0.59% | 7,585.24 |
| **2022** | **126.72** | **1.62%** | 7,819.57 |
| 2023 | 82.93 | 1.00% | 8,275.42 |
| 2024 | 85.50 | 1.01% | 8,479.70 |
| 2025 | 57.67 | 0.58% | 9,987.91 |
| 2026 (ene-jun, parcial) | 35.37 | 0.70% | 5,060.60 |

Notas clave, tal como las documenta el propio BCR en la serie:

- **2019 y 2020 no son ceros — son ausencia del canal.** El BCR lo aclara explícitamente en la nota de la serie: *"SIN DESGLOSE DISPONIBLE: el canal no existía en la estadística del BCR. La serie de billeteras digitales de criptomonedas inicia en septiembre de 2021 (primer dato: 1.99 MM US$), tras la entrada en vigencia de la Ley Bitcoin."* Esto es un dato editorial fuerte por sí solo: el canal nace con la ley, no antes.
- **2022 es el pico: USD 126.72 millones, 1.62% de todas las remesas recibidas ese año.**
- Caída sostenida desde el pico: 2023 (USD 82.93M) → 2024 (USD 85.50M, prácticamente estable) → 2025 (USD 57.67M, la caída más marcada).
- El dato de 2026 es **parcial** (solo enero-junio, cifras preliminares) — no comparar directamente contra los totales anuales de años completos.
- Las cifras de 2024 y 2025 están marcadas por el propio BCR como "sujeto a revisión" (revisan hasta 3 años previos cada enero) — vale la pena volver a chequear la serie antes de publicar, por si hay una actualización más reciente.

**La reversión de la Ley Bitcoin (enero 2025):** el 29 de enero de 2025, la Asamblea Legislativa de El Salvador reformó la Ley Bitcoin para quitarle el carácter de moneda de curso legal obligatorio — el aceptar bitcoin pasó a ser voluntario para el sector privado, y los pagos de impuestos quedaron limitados a dólares. La reforma fue una condición ("prior action") del acuerdo por USD 1,400 millones que El Salvador cerró con el FMI, aprobado por el Directorio Ejecutivo del FMI el 26 de febrero de 2025 (con un desembolso inmediato de unos USD 113 millones). El propio comunicado oficial del FMI lo describe así: *"Prior actions include legal reforms that have made acceptance of Bitcoin by the private sector voluntary and ensured that tax payments are made only in U.S. dollars."*

Esto encaja con la serie del BCR: la caída del canal cripto/billeteras de 2024 (USD 85.50M) a 2025 (USD 57.67M) coincide con el año en que dejó de ser obligatorio aceptarlo — una lectura editorial razonable, aunque el BCR no publica el desglose mensual dentro del año como para aislar el efecto exacto de la reforma de enero vs. el resto del año.

**Por qué importa:** es la macro — cuánto entra al país, y por qué canal. Es dato ya publicado y auditable por el propio BCR, así que es la pieza que se puede publicar **ya**, sin esperar a que madure el monitor de operadores.

### Pieza de seguimiento — en desarrollo: el monitor de seis operadores

Esta es la pieza que responde la pregunta que el dato del BCR no puede responder por sí solo: de lo que se envía, **¿cuánto se queda en el camino, y varía según qué operador elija la persona que envía?**

- Seis operadores monitoreados: Western Union, MoneyGram, Ria, Remitly, Xoom y RemitBee.
- Dos corredores: EE.UU.→El Salvador y **Canadá→El Salvador**.
- Metodología adaptada de RPW del Banco Mundial, con evidencia verificable (captura + hash SHA-256) detrás de cada cifra — ver `docs/methodology.md` para el detalle completo.
- **Estado real a la fecha de este borrador: fase temprana.** 341 observaciones, 3 días distintos de datos, arrancado el 31/07–01/08/2026. Es una fotografía inicial confiable, no todavía una serie con densidad suficiente para hablar de tendencias. Esto se debe decir así de claro en el pitch — no vender el monitor como algo más maduro de lo que es.

## El diferencial: el corredor Canadá→El Salvador

Hasta donde pudimos confirmar, el corredor **Canadá→El Salvador no está cubierto por RPW** del Banco Mundial ni por ningún otro monitor público conocido de costo de remesas. La diáspora salvadoreña en Canadá existe y envía dinero, pero no hay ningún dato público sistemático sobre cuánto le cuesta hacerlo. Este proyecto es, hasta donde sabemos, el primer relevamiento sistemático y con evidencia verificable de ese corredor específico.

*(Vale la pena verificar esta afirmación una vez más antes de publicarla como diferencial — "hasta donde pudimos confirmar" no es lo mismo que "confirmado exhaustivamente". Si tenés forma de chequear el listado de corredores de RPW de forma más directa antes de irte, mejor.)*

## Por qué ahora / por qué esta ventana

*(Espacio para que completes vos: cualquier gancho de actualidad — cambios regulatorios, estacionalidad de remesas, algo específico del contexto salvadoreño del momento — que yo no tengo forma de verificar desde acá.)*

## Qué falta para publicar cada pieza

**BCR (ancla):**
- [x] Insertar cifras reales (ver tabla arriba, fuente `data/bcr/bcr-serie-canales.csv`).
- [x] Confirmar fuente exacta y fecha de corte (BCR/BDEF, datos hasta junio 2026, consultado 2026-07-31).
- [x] Verificar y citar la reforma de la Ley Bitcoin de enero 2025 (ver sección de arriba y Fuentes).
- [ ] Armar el gráfico/visualización a partir del CSV.
- [ ] Redacción final.

**Monitor de operadores (seguimiento):**
- [ ] Completar primera carga manual de MoneyGram (`collectors/manual_entry.py`) — hoy tiene 0 observaciones.
- [ ] Acumular más días de datos antes de mostrar cualquier comparación entre operadores como si fuera estable.
- [ ] Enviar y esperar respuesta (o vencimiento de plazo) de las cartas de derecho de réplica — ver `docs/right-of-reply-templates.md`.
- [ ] Resolver la fragilidad conocida del cotizador de Ria (ver limitaciones en `docs/methodology.md`) o al menos documentarla en la nota si sigue apareciendo.

## Fuentes

- Banco Central de Reserva de El Salvador (BDEF), serie "Ingresos de remesas familiares por agente liquidador" — [`data/bcr/bcr-serie-canales.csv`](../data/bcr/bcr-serie-canales.csv) en este repo, datos hasta junio 2026, consultada 2026-07-31.
- IMF Executive Board Approves New 40-month US$1.4 billion Extended Fund Facility Arrangement for El Salvador — [comunicado oficial del FMI, 2025-02-26](https://www.imf.org/en/news/articles/2025/02/26/pr25043-el-salvador-imf-approves-new-40-month-us1-bn-eff-arr).
- El Salvador's Bitcoin Law Changes To Secure IMF Funding — [Forbes, 2025-02-28](https://www.forbes.com/sites/digital-assets/2025/02/28/el-salvadors-bitcoin-law-changes-to-secure-imf-funding/), con el detalle de fecha de la reforma legislativa (29 de enero de 2025).

*(No pude acceder directo a Reuters ni AP — el crawler de búsqueda las bloquea a nivel de dominio. Si tenés acceso a una nota de Reuters/AP específica, mejor citarla en lugar de o junto con Forbes; el comunicado del FMI como fuente primaria no cambia.)*

## Notas de proceso (para vos, no para el pitch)

- Todo el pipeline corre en GitHub Actions, diario, con evidencia versionada y hash verificable — si un editor pregunta "¿cómo sabemos que esto es real y no un scrape roto?", la respuesta corta es: cada cifra tiene una captura de pantalla con hash SHA-256 detrás, documentado en `docs/methodology.md`.
- El dataset sigue creciendo todos los días de forma automática aunque no estés — pero **no** de forma desatendida del todo: dejá a alguien revisando el correo de derecho de réplica y los logs de las corridas mientras no estés, si el plan es publicar en tu ausencia.
