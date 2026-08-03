# Pitch — El costo real de enviar remesas a El Salvador

> Borrador de pitch. Falta tono editorial, título de gancho, y — importante — **los datos reales de la serie del BCR** (ver nota roja más abajo). No mandar así.

## Resumen en una línea

El Salvador recibe remesas equivalentes a cerca de un cuarto de su PIB. Sabemos cuánto entra al país (BCR lo publica desde hace años). Lo que nadie documenta con evidencia verificable, día a día, es **cuánto se pierde en el camino** — y ese es el vacío que este proyecto llena.

## Dos piezas, dos velocidades

### Pieza ancla — ya publicable: la serie histórica del BCR (2019-2026)

> ⚠️ **NOTA — falta contenido real.** No encontré ningún archivo con la serie del BCR en este repositorio (revisé `exports/`, `docs/`, y el resto del árbol — no hay nada) ni en el historial de este proyecto. Esta sección está armada como **placeholder estructural**: la narrativa y el rol que cumple dentro del pitch, no los números. Antes de enviar esto hay que:
> 1. Confirmar dónde vive esa serie (¿otro repo? ¿una planilla local? ¿scrapeada de la web del BCR?).
> 2. Insertar acá el gráfico/cifras reales y las fuentes exactas (series, cuadros, URLs del BCR).
> 3. Verificar que los números citados en el resto de esta sección sean reales antes de que salgan de tu computadora.

Contenido esperado de esta pieza (a completar):
- Serie mensual/anual de remesas recibidas por El Salvador, 2019–2026, según el Banco Central de Reserva.
- Contexto: cómo se mueve la serie contra hitos conocidos del período (pandemia 2020, adopción de bitcoin como moneda de curso legal en 2021, ciclos migratorios, política migratoria de EE.UU.).
- Por qué importa: es la macro — cuánto entra al país. Es dato ya publicado y auditable, así que es la pieza que se puede publicar **ya**, sin esperar al monitor de operadores.

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
- [ ] Insertar datos/gráfico reales.
- [ ] Confirmar fuente exacta y fecha de corte.
- [ ] Redacción final.

**Monitor de operadores (seguimiento):**
- [ ] Completar primera carga manual de MoneyGram (`collectors/manual_entry.py`) — hoy tiene 0 observaciones.
- [ ] Acumular más días de datos antes de mostrar cualquier comparación entre operadores como si fuera estable.
- [ ] Enviar y esperar respuesta (o vencimiento de plazo) de las cartas de derecho de réplica — ver `docs/right-of-reply-templates.md`.
- [ ] Resolver la fragilidad conocida del cotizador de Ria (ver limitaciones en `docs/methodology.md`) o al menos documentarla en la nota si sigue apareciendo.

## Notas de proceso (para vos, no para el pitch)

- Todo el pipeline corre en GitHub Actions, diario, con evidencia versionada y hash verificable — si un editor pregunta "¿cómo sabemos que esto es real y no un scrape roto?", la respuesta corta es: cada cifra tiene una captura de pantalla con hash SHA-256 detrás, documentado en `docs/methodology.md`.
- El dataset sigue creciendo todos los días de forma automática aunque no estés — pero **no** de forma desatendida del todo: dejá a alguien revisando el correo de derecho de réplica y los logs de las corridas mientras no estés, si el plan es publicar en tu ausencia.
