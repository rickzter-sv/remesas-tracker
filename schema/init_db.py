"""Crea la base de datos SQLite del rastreador de remesas a partir de schema.sql.

Uso:
    python schema/init_db.py [ruta_destino.db]

Si no se especifica ruta_destino, se crea remesas.db en la raiz del repo.
Si la base de datos ya existe, el script se detiene sin sobrescribirla.
"""

import sqlite3
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).parent.parent / "remesas.db"


def init_db(db_path: Path) -> None:
    if db_path.exists():
        sys.exit(f"Error: ya existe una base de datos en {db_path}. Bórrala o elige otra ruta.")

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()

    print(f"Base de datos creada en: {db_path}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    init_db(target)
