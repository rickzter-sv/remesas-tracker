# Metodología — Remesas Tracker

> Borrador. Escrito a partir de `README.md`, `schema/schema.sql` y `collectors/utils.py` del propio repositorio. Falta ajuste de tono/estilo editorial antes de publicar.

## 1. Qué mide este proyecto

Remesas Tracker documenta, con evidencia verificable, cuánto cuesta realmente enviar dinero desde **Estados Unidos** y **Canadá** hacia **El Salvador**, a través de seis operadores: Western Union, MoneyGram, Ria Money Transfer, Remitly, Xoom (un servicio de PayPal) y RemitBee.

El costo que se mide no es la tarifa que un operador anuncia en su publicidad, sino la que efectivamente aparece en su cotizador público al simular un envío real, para un monto y método de pago específicos, un día específico.

## 2. Fuente de los datos

Cada observación se obtiene directamente del **cotizador público** de cada operador (la calculadora que cualquier persona puede usar sin crear una cuenta), no de tarifarios publicados ni de agregadores de terceros:

- **Western Union, Ria, Xoom, RemitBee**: navegador automatizado (Playwright) que simula la interacción de una persona real — completa el monto, selecciona método de pago/entrega, y lee la tarifa que el sitio muestra.
- **Remitly**: se lee directamente la tabla de tarifas ya renderizada en el HTML público (no requiere ejecutar JavaScript).
- **MoneyGram**: su sitio bloquea automatización de forma explícita (ver sección 5, Limitaciones) — la recolección es manual, con la misma disciplina de registro que el resto.

Corredores cubiertos: **US→SV** y **CA→SV**. Montos de referencia: **$100, $200 y $500** — montos escalonados, no solo uno, porque muchas comisiones son fijas (no proporcionales), así que el costo como porcentaje del envío cambia según el monto.

## 3. Metodología adaptada de Remittance Prices Worldwide (RPW) del Banco Mundial

El concepto central se apoya en el que usa **RPW**, la iniciativa del Banco Mundial que releva el costo de las remesas a nivel global desde 2008 y que sustenta la meta 10.c de los Objetivos de Desarrollo Sostenible (bajar el costo promedio global por debajo del 3%):

**costo total = comisión fija + margen implícito en el tipo de cambio**, expresado como porcentaje del monto enviado.

Diferencias explícitas respecto al protocolo de RPW, para que quede claro qué se adapta y qué no:

- RPW releva operadores por encuesta directa (telefónica, presencial y web, con protocolo auditado trimestralmente) y distingue explícitamente canal en efectivo vs. canal digital/de cuenta a cuenta. Este proyecto releva **exclusivamente el canal digital de autoservicio** (el cotizador web público), así que es comparable al subíndice "online" de RPW para los operadores que lo tienen, no necesariamente a sus cifras de red de agentes en efectivo.
- RPW usa habitualmente $200 como monto de referencia estándar para comparabilidad entre corredores. Este proyecto usa $100/$200/$500 para poder mostrar cómo cambia el costo porcentual según el monto, algo que un solo punto de referencia no revela.

## 4. Disciplina anti-promoción

Regla que gobierna toda la recolección (documentada también en `schema/schema.sql` y `README.md`):

> Nunca se guarda un precio promocional de primer envío como si fuera el costo real. Siempre se captura la tarifa de **lista** — la que paga alguien que ya usó el servicio antes. Si en algún momento se captura una tarifa promocional, se marca explícitamente `is_promotional = 1` y esa fila **nunca** se usa para comparar costos reales entre operadores.

Cómo se aplica en la práctica, caso por caso (cada colector documenta sus propias verificaciones, hechas contra el sitio real antes de automatizarlas):

- Detección de texto tachado/`line-through` en el precio mostrado (indicador de "antes/ahora").
- Detección de badges tipo "NN% OFF $X.XX".
- Búsqueda de palabras clave: *first transfer*, *new customer*, *welcome rate*, *welcome offer*.
- Casos verificados de tarifas en $0.00 que **no** son promoción de bienvenida sino condición permanente del producto (ej. PayPal USD en Xoom, o envíos de $500+ por ciertos métodos en RemitBee) — documentados explícitamente en el código del colector correspondiente, con nota aclaratoria guardada junto a la observación.
- Casos verificados de tipo de cambio con doble cotización promocional/lista (Ria en el corredor CA→SV) — se guarda siempre la tasa de lista, nunca la promocional, incluso cuando el sitio la muestra destacada por defecto.

## 5. Evidencia y trazabilidad

Cada observación queda respaldada por un archivo de evidencia (captura de pantalla o HTML guardado) con **hash SHA-256**, de forma que cualquier cifra publicada puede verificarse contra la captura exacta que la originó. Esto incluye las cargas manuales de MoneyGram, que pasan por el mismo mecanismo (`collectors/manual_entry.py`).

Nota operativa: las capturas se almacenan como JPEG (no PNG) para mantener el tamaño del repositorio sostenible en una recolección diaria continua; existe además una política de retención que puede eliminar el archivo binario de capturas viejas del disco, pero el hash SHA-256 permanece siempre como prueba de que la captura existió y con qué contenido exacto, incluso si el archivo ya no está.

## 6. Limitaciones

- **MoneyGram — bloqueo anti-bot confirmado.** El sitio de MoneyGram detecta automatización de forma explícita (mensaje directo de bloqueo, no un CAPTCHA resoluble). Se decidió no intentar evadirlo bajo ninguna circunstancia. La recolección es manual, con menor frecuencia (cadencia propuesta: semanal) por el costo de tiempo que implica, y por lo tanto con menos densidad de datos que los otros cinco operadores.
- **Corredor CA→SV sin precedente conocido en RPW.** Hasta donde pudimos confirmar, el corredor Canadá→El Salvador no figura entre los corredores relevados regularmente por RPW — lo que significa que este proyecto llena un vacío de datos público real, pero también que no existe una referencia externa auditada contra la cual validar directamente las cifras de ese corredor.
- **Dataset en fase temprana de acumulación.** La recolección automatizada de los seis operadores arrancó recién el 31 de julio–1 de agosto de 2026. A la fecha de este borrador, la base cubre **3 días distintos de datos** (341 observaciones totales). Esto alcanza para documentar una fotografía confiable del costo actual, pero **todavía no es una serie temporal** — no debe usarse para afirmar tendencias, estacionalidad, ni comparaciones mes a mes.
- **Cobertura de métodos no simétrica entre operadores.** No todos los operadores exponen las mismas combinaciones de método de pago/entrega (ej. Remitly en EE.UU. no distingue método de pago, solo de entrega; RemitBee solo opera en el corredor CA→SV). Las comparaciones entre operadores deben controlar por método, no solo por operador y monto.
- **Fragilidad conocida del cotizador de Ria.** Se confirmó (reproducido de forma independiente desde otro origen de red no residencial) que el cotizador de Ria falla intermitentemente devolviendo "Unable to get rates" — no parece ser un bloqueo anti-bot explícito, sino una falla del backend de tasas de Ria bajo ciertas condiciones de red. Se agregaron reintentos automáticos para mitigarlo, pero en un mal día la fila de Ria puede faltar por esta falla, no por un cambio real de precio.

## 7. Estado actual del dataset (a completar antes de publicar)

- Observaciones totales: **341** (dato al 2026-08-03; crece con cada corrida diaria).
- Días distintos con datos: **3**.
- MoneyGram: **0 observaciones cargadas todavía** — el flujo de muestreo manual (`collectors/manual_entry.py`) está listo pero aún no se corrió la primera carga real.
- Los 5 operadores automatizados corren diariamente vía GitHub Actions (09:15 UTC).

*(Actualizar esta sección con las cifras del día antes de enviar el pitch — estos números cambian todos los días.)*
