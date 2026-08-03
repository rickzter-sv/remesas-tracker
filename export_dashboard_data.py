#!/usr/bin/env python3
"""
export_dashboard_data.py

Exporta la base SQLite de remesas-tracker a un JSON liviano que consume
el dashboard estático (docs/index.html) via GitHub Pages.

USO:
    python export_dashboard_data.py

Se ejecuta desde la raíz del proyecto (remesas-tracker/); la base vive en
remesas.db en la raíz (no en db/).

Columnas verificadas contra el schema real (sqlite3 remesas.db ".schema")
el 2026-08-03 — ver docs/pitch/schema-notes.md para el detalle de cada
corrección aplicada respecto al supuesto original.
"""

import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("remesas.db")
OUTPUT_PATH = Path("docs/data.json")

def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        sys.exit(f"ERROR: no se encontró la base en {db_path}. "
                  f"Ajusta DB_PATH en este script si tu ruta es distinta.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_operators(conn):
    rows = conn.execute("""
        SELECT operator_id AS id, name, requires_manual_sampling
        FROM operators
    """).fetchall()
    return [dict(r) for r in rows]

def fetch_corridors(conn):
    rows = conn.execute("""
        SELECT corridor_id AS id, origin_country, destination_country,
               origin_currency AS currency_origin, destination_currency AS currency_destination
        FROM corridors
    """).fetchall()
    return [dict(r) for r in rows]

def fetch_observations(conn, days_back=90):
    # Trae observaciones recientes; ajusta days_back si quieres el histórico completo.
    # NOTA: fx_margin_pct se exporta como 0.0 porque no existe como columna
    # real — ver docs/pitch/schema-notes.md, hallazgo #6.
    # run_type='scheduled' excluye tanto observaciones pre-producción (Ria,
    # lote inicial de Xoom) como cualquier corrida manual_test futura — ver
    # docs/pitch/schema-notes.md, sección 7.
    rows = conn.execute(f"""
        SELECT
            o.observation_id AS id,
            o.operator_id,
            op.name AS operator_name,
            o.corridor_id,
            c.origin_country,
            c.destination_country,
            o.send_amount,
            o.fee AS commission_fee,
            0.0 AS fx_margin_pct,
            o.total_cost_pct,
            o.is_promotional,
            o.delivery_method,
            o.timestamp_utc AS observed_at
        FROM observations o
        JOIN operators op ON op.operator_id = o.operator_id
        JOIN corridors c ON c.corridor_id = o.corridor_id
        WHERE o.timestamp_utc >= datetime('now', '-{int(days_back)} days')
          AND o.run_type = 'scheduled'
        ORDER BY o.timestamp_utc ASC
    """).fetchall()
    return [dict(r) for r in rows]

def fetch_evidence_counts(conn):
    # Conteo de evidencia por operador, útil para mostrar "última verificación".
    # evidence no tiene operator_id propio; se deriva via JOIN con observations.
    rows = conn.execute("""
        SELECT o.operator_id AS operator_id,
               COUNT(*) as evidence_count,
               MAX(e.captured_at) as last_evidence_at
        FROM evidence e
        JOIN observations o ON o.observation_id = e.observation_id
        GROUP BY o.operator_id
    """).fetchall()
    return [dict(r) for r in rows]

def main():
    conn = connect(DB_PATH)
    try:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "operators": fetch_operators(conn),
            "corridors": fetch_corridors(conn),
            "observations": fetch_observations(conn),
            "evidence_summary": fetch_evidence_counts(conn),
        }
    finally:
        conn.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    print(f"OK: {len(payload['observations'])} observaciones exportadas a {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
