# Remesas Tracker

Rastreador de costos de remesas enviadas desde Estados Unidos y Canadá hacia El Salvador. Proyecto de periodismo de datos: recolecta y documenta, con evidencia verificable, cuánto cuesta realmente enviar dinero a través de distintos operadores.

## Estructura del repositorio

- `collectors/` — scripts de recolección (scrapers) por operador.
- `schema/` — esquema de la base de datos SQLite y script de inicialización.
- `evidence/` — capturas/archivos que respaldan cada observación (screenshots, HTML guardado, etc.), referenciados por hash SHA-256 en la tabla `evidence`.
- `exports/` — exportaciones generadas a partir de la base de datos (CSV, JSON, etc.) para publicación o análisis.

## Regla de recolección de datos: nunca usar precios promocionales de primer envío

Varios operadores (por ejemplo RemitBee) ofrecen el **primer envío con tarifa promocional de $0.00** para atraer nuevos usuarios. Esa tarifa **no representa el costo real** del servicio para un usuario recurrente.

**Regla:** nunca se guarda el precio promocional de primer envío como si fuera la tarifa real. Siempre se debe capturar la **tarifa de lista** (la que paga un usuario que ya usó el servicio antes, sin promociones de bienvenida). Si en algún momento se captura una observación con precio promocional, debe marcarse explícitamente con `is_promotional = 1` en la tabla `observations`, y esas filas **nunca** deben usarse para comparar el costo real entre operadores.

Esta misma regla está documentada como comentario en [`schema/schema.sql`](schema/schema.sql).

## Base de datos

El esquema vive en [`schema/schema.sql`](schema/schema.sql) y define cuatro tablas:

- `operators` — operadores de remesas (Western Union, MoneyGram, Ria, Remitly, Xoom, RemitBee), con el corredor que cubren y si su cotización requiere JavaScript/Playwright.
- `corridors` — corredores origen→destino (US→SV, CA→SV) con sus monedas y si requieren conversión de divisa.
- `observations` — cada cotización capturada: monto, comisión, tipo de cambio, monto recibido, costo total %, si es promocional, método de recolección y URL fuente.
- `evidence` — archivo de evidencia (con hash SHA-256) que respalda cada observación.

Para crear la base de datos vacía:

```bash
python schema/init_db.py
```

Esto genera `remesas.db` en la raíz del repo, con las tablas creadas y los operadores/corredores precargados, pero sin observaciones todavía.

## Estado actual

Este repositorio contiene únicamente el esqueleto del proyecto (estructura, esquema, script de inicialización). La lógica de scraping por operador aún no está implementada.
