-- Rastreador de costos de remesas EE.UU./Canadá -> El Salvador
-- Esquema SQLite
--
-- REGLA CRITICA DE RECOLECCION DE DATOS:
-- Muchos operadores (ej. RemitBee) ofrecen el primer envio con tarifa
-- promocional de $0.00 para atraer nuevos usuarios. NUNCA se debe guardar
-- ese precio promocional como si fuera la tarifa real/recurrente del
-- servicio. Siempre capturar y registrar la tarifa de LISTA (la que paga
-- un usuario recurrente, sin promociones de bienvenida). Si en algun
-- momento se captura una observacion promocional, debe marcarse
-- explicitamente con is_promotional = 1 y nunca usarse para comparaciones
-- de costo "real" entre operadores.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS operators (
    operator_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    type                    TEXT NOT NULL,          -- ej. 'bank', 'mto' (money transfer operator), 'fintech'
    registered_country      TEXT NOT NULL,
    corridors_served        TEXT,                   -- lista separada por comas, ej. 'US-SV,CA-SV'
    website                 TEXT,
    -- 1 si el sitio del operador bloquea automatizacion de forma explicita
    -- (mensaje de deteccion de bots tipo DataDome, no un CAPTCHA resoluble):
    -- en ese caso NO se debe intentar evadir el bloqueo bajo ninguna
    -- circunstancia (nada de browsers "stealth", rotacion de user-agent/IP,
    -- ni similares); la recoleccion para ese operador debe ser manual
    -- (collection_method = 'manual') hasta nuevo aviso.
    requires_manual_sampling INTEGER NOT NULL DEFAULT 0,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS corridors (
    corridor_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_country          TEXT NOT NULL,
    destination_country     TEXT NOT NULL,
    origin_currency          TEXT NOT NULL,
    destination_currency    TEXT NOT NULL,
    requires_fx_conversion  INTEGER NOT NULL DEFAULT 1,  -- 0/1 boolean
    UNIQUE (origin_country, destination_country)
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc           TEXT NOT NULL,       -- ISO 8601 UTC
    corridor_id             INTEGER NOT NULL REFERENCES corridors(corridor_id),
    operator_id              INTEGER NOT NULL REFERENCES operators(operator_id),
    send_amount              REAL NOT NULL,
    funding_method           TEXT,                -- ej. 'debit_card', 'bank_account', 'cash'
    delivery_method          TEXT,                -- ej. 'cash_pickup', 'bank_deposit', 'mobile_wallet'
    fee                      REAL NOT NULL,
    exchange_rate_applied    REAL,
    receive_amount           REAL,
    total_cost_pct           REAL,                -- costo total como % del monto enviado
    is_promotional           INTEGER NOT NULL DEFAULT 0,  -- 1 si es tarifa promocional (NUNCA usar como costo real)
    collection_method        TEXT NOT NULL,       -- ej. 'manual', 'scraper_playwright', 'scraper_requests'
    source_url               TEXT NOT NULL,
    collector_notes          TEXT,
    -- Origen de la corrida que genero esta fila, para poder excluir del
    -- export publico (export_dashboard_data.py) y proteger de purga
    -- (scripts/prune_evidence.py) cualquier cosa que no sea la corrida
    -- oficial diaria:
    --   'scheduled'           - cron oficial (GITHUB_EVENT_NAME=='schedule')
    --   'manual_test'         - workflow_dispatch, o cualquier corrida local/
    --                           de desarrollo sin GITHUB_EVENT_NAME=='schedule'
    --                           (incluye tambien corridas locales sin ese env var)
    --   'pre_production_test' - observaciones de antes de que la automatizacion
    --                           de ese colector se comiteara (ver docs/pitch/
    --                           schema-notes.md, hallazgo del 2026-08-03)
    -- Default 'manual_test' a proposito: si algun colector nuevo olvida
    -- setear este campo, el fallo seguro es "queda fuera del export publico
    -- y protegido de purga", nunca lo contrario.
    run_type                 TEXT NOT NULL DEFAULT 'manual_test'
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id    INTEGER NOT NULL REFERENCES observations(observation_id),
    file_path         TEXT NOT NULL,       -- ruta relativa dentro de /evidence
    sha256_hash       TEXT NOT NULL,
    captured_at       TEXT NOT NULL,       -- ISO 8601 UTC
    -- 'available': el archivo en file_path existe hoy en el repo.
    -- 'file_missing': se confirmo que el archivo ya no existe (purgado como
    -- evidencia de una corrida de prueba, o nunca llego a comitearse) -- el
    -- sha256_hash sigue siendo la prueba de que se capturo y con que
    -- contenido, pero no hay archivo que un tercero pueda verificar contra
    -- ese hash. Ver docs/pitch/schema-notes.md para el detalle del
    -- diagnostico que origino este campo (2026-08-03).
    file_status       TEXT NOT NULL DEFAULT 'available'
);

CREATE INDEX IF NOT EXISTS idx_observations_corridor ON observations(corridor_id);
CREATE INDEX IF NOT EXISTS idx_observations_operator ON observations(operator_id);
CREATE INDEX IF NOT EXISTS idx_observations_timestamp ON observations(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_evidence_observation ON evidence(observation_id);

-- ---------------------------------------------------------------------------
-- Datos precargados: corredores
-- ---------------------------------------------------------------------------
INSERT INTO corridors (origin_country, destination_country, origin_currency, destination_currency, requires_fx_conversion)
VALUES
    ('US', 'SV', 'USD', 'USD', 0),  -- El Salvador esta dolarizado: US->SV no requiere conversion de divisa
    ('CA', 'SV', 'CAD', 'USD', 1);

-- ---------------------------------------------------------------------------
-- Datos precargados: operadores
-- URLs de cotizador verificadas manualmente (ver notes de cada fila).
-- ---------------------------------------------------------------------------
INSERT INTO operators (name, type, registered_country, corridors_served, website, notes)
VALUES
    ('Western Union', 'mto', 'US', 'US-SV,CA-SV',
     'https://www.westernunion.com/us/en/web/send-money/start',
     'Cotizador US verificado 2026-07-31 (HTTP 200). Cotizador CA: https://www.westernunion.com/ca/en/web/send-money/start. Requiere Playwright (contenido dinamico via JS).'),

    ('MoneyGram', 'mto', 'US', 'US-SV,CA-SV',
     'https://www.moneygram.com/mgo/us/en/',
     'Cotizador verificado 2026-07-31 (HTTP 200). Bloqueo anti-bot confirmado 2026-08-01 via Playwright -- '
     'mensaje explicito de deteccion de automatizacion ("Access is temporarily restricted... '
     'Automated (bot) activity on your network"), no un CAPTCHA resoluble. No intentar evadirlo. '
     'Requiere muestreo manual (collection_method = ''manual'').'),

    ('Ria Money Transfer', 'mto', 'US', 'US-SV,CA-SV',
     'https://www.riamoneytransfer.com/en-us',
     'Cotizador verificado 2026-07-31 (HTTP 200). Requiere Playwright (contenido dinamico via JS).'),

    ('Remitly', 'fintech', 'US', 'US-SV,CA-SV',
     'https://www.remitly.com/us/en/el-salvador',
     'Cotizador verificado 2026-07-31 (HTTP 200). Cotizador CA: https://www.remitly.com/ca/en/el-salvador. Scrapeable SIN JS (HTML renderizado en servidor / requests+BeautifulSoup, no requiere Playwright).'),

    ('Xoom (a PayPal service)', 'fintech', 'US', 'US-SV,CA-SV',
     'https://www.xoom.com/el-salvador/send-money',
     'Cotizador verificado 2026-07-31 (HTTP 200). Cotizador CA: https://www.xoom.com/canada/send-money. Requiere Playwright (contenido dinamico via JS).'),

    ('RemitBee', 'fintech', 'CA', 'CA-SV',
     'https://www.remitbee.com/send-money/el-salvador',
     'Cotizador verificado 2026-07-31 via navegador (curl da 403 por proteccion anti-bot; requiere navegador real). Solo corredor CA->SV. Requiere Playwright. ADVERTENCIA: la pagina anuncia el primer envio gratis ("Your very first transfer is completely free") -- NO capturar ese precio como tarifa real, siempre registrar la tarifa de lista para envios subsecuentes.');

UPDATE operators SET requires_manual_sampling = 1 WHERE name = 'MoneyGram';

-- Ria Money Transfer: decision aplicada por defecto el 2026-08-03, pendiente
-- de confirmacion del usuario (ver operators.notes para el detalle y docs/
-- pitch/schema-notes.md). El fallo real fue un TimeoutError de Playwright
-- esperando un dropdown (selector roto), NO un bloqueo anti-bot como
-- MoneyGram -- reproducido de forma consistente en 3 corridas distintas
-- (2026-08-03T04:23, 02:15 y 12:35 UTC).
UPDATE operators SET requires_manual_sampling = 1 WHERE name = 'Ria Money Transfer';
