"""Orquestador: corre los 5 colectores automatizados en secuencia.

Cada colector corre como subproceso independiente (misma invocacion que se
usa manualmente: `python collectors/<script>.py <db_path>`), asi que un
crash o cuelgue de Playwright en uno no puede corromper la conexion a la
base de datos de otro -- cada colector abre y cierra su propia conexion.

Si un colector falla (timeout, selector roto, bloqueo anti-bot nuevo, lo
que sea), el orquestador captura el error, lo registra con timestamp, y
sigue con el siguiente. El numero de observaciones insertadas por cada
colector se mide comparando COUNT(*) en la tabla `observations` antes y
despues de la corrida (incluso si el colector fallo a medias, esto refleja
lo que realmente quedo insertado, no lo que el colector dijo que iba a
insertar).

MoneyGram NO esta en la lista: su sitio bloquea automatizacion de forma
explicita (ver operators.notes / operators.requires_manual_sampling=1).
Este orquestador nunca lo intenta.

Uso:
    python collectors/run_all.py [ruta_a_remesas.db]

Codigo de salida: 0 si todos los colectores corrieron sin errores, 1 si al
menos uno fallo (util para que un paso de CI lo detecte, sin que eso
implique que la corrida se detuvo a medio camino -- todos los colectores
se ejecutan siempre, hayan fallado los anteriores o no).
"""

import argparse
import subprocess
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COLLECTORS_DIR = Path(__file__).parent
DEFAULT_DB_PATH = REPO_ROOT / "remesas.db"
LOG_DIR = REPO_ROOT / "logs"

# Orden pedido explicitamente: remitly, western_union, ria, xoom, remitbee.
COLLECTOR_SCRIPTS = [
    ("Remitly", "remitly.py"),
    ("Western Union", "western_union.py"),
    ("Ria Money Transfer", "ria.py"),
    ("Xoom (a PayPal service)", "xoom.py"),
    ("RemitBee", "remitbee.py"),
]

MONEYGRAM_REMINDER = (
    "RECORDATORIO: MoneyGram sigue pendiente de muestreo manual "
    "(operators.requires_manual_sampling=1) -- su sitio bloquea automatizacion "
    "de forma explicita (ver operators.notes, confirmado 2026-08-01). Este "
    "orquestador NO lo corre ni lo intenta: no existe collectors/moneygram.py "
    "a proposito."
)

TIMEOUT_SECONDS = 600


def count_observations(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    finally:
        conn.close()


def run_collector(name: str, script_name: str, db_path: Path, log_file) -> dict:
    script_path = COLLECTORS_DIR / script_name

    def log(line: str) -> None:
        stamped = f"[{datetime.now(timezone.utc).isoformat()}] {line}"
        print(stamped)
        log_file.write(stamped + "\n")
        log_file.flush()

    log(f"=== Iniciando {name} ({script_name}) ===")
    before = count_observations(db_path)
    result = {"name": name, "script": script_name, "inserted": 0, "ok": False, "error": None}

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path), str(db_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        result["inserted"] = count_observations(db_path) - before
        for line in proc.stdout.strip().splitlines():
            log(f"  [{name}] {line}")
        if proc.returncode != 0:
            error_tail = "\n".join(proc.stderr.strip().splitlines()[-15:]) or "(sin salida de error en stderr)"
            result["error"] = f"codigo de salida {proc.returncode}: {error_tail}"
            log(f"FALLO {name}: {result['error']}")
        else:
            result["ok"] = True
            log(f"OK {name}: {result['inserted']} observaciones nuevas")
    except subprocess.TimeoutExpired:
        result["inserted"] = count_observations(db_path) - before
        result["error"] = f"timeout tras {TIMEOUT_SECONDS}s sin terminar"
        log(f"FALLO {name}: {result['error']}")
    except Exception as exc:  # noqa: BLE001 -- este orquestador debe sobrevivir a CUALQUIER fallo de un colector
        result["inserted"] = count_observations(db_path) - before
        result["error"] = f"{type(exc).__name__}: {exc}"
        log(f"FALLO {name}: {result['error']}")

    return result


def print_summary(results: list, log_path: Path) -> None:
    print()
    print("=" * 72)
    print("RESUMEN DE LA CORRIDA")
    print("=" * 72)
    total_inserted = 0
    failures = []
    for r in results:
        status = "OK" if r["ok"] else "FALLO"
        print(f"  {r['name']:<25} {status:<6} {r['inserted']:>4} observaciones nuevas")
        total_inserted += r["inserted"]
        if not r["ok"]:
            failures.append(r)

    print("-" * 72)
    print(f"Total observaciones nuevas insertadas en esta corrida: {total_inserted}")

    if failures:
        print(f"\n{len(failures)} de {len(results)} colector(es) fallaron:")
        for r in failures:
            print(f"  - {r['name']}: {r['error']}")
    else:
        print(f"\nLos {len(results)} colectores corrieron sin errores.")

    print(f"\n{MONEYGRAM_REMINDER}")
    print(f"\nLog completo de esta corrida: {log_path.relative_to(REPO_ROOT)}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db_path", nargs="?", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        sys.exit(f"Error: no existe la base de datos en {db_path}. Corre schema/init_db.py primero.")

    LOG_DIR.mkdir(exist_ok=True)
    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"run_all_{run_timestamp}.log"

    results = []
    with log_path.open("w", encoding="utf-8") as log_file:
        for name, script_name in COLLECTOR_SCRIPTS:
            results.append(run_collector(name, script_name, db_path, log_file))
        log_file.write(f"\n{MONEYGRAM_REMINDER}\n")

    print_summary(results, log_path)

    if any(not r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
