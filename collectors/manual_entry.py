"""Registro manual de tarifas para operadores con requires_manual_sampling=1
(hoy: MoneyGram, bloqueado por deteccion anti-bot -- ver schema.sql).

CLI interactivo por prompts, pensado para minimizar friccion en una sesion de
muestreo manual recurrente. Reusa las mismas funciones de collectors/utils.py
que los colectores automatizados (get_operator_id, get_corridor_id,
insert_observation_if_new, insert_evidence), asi que una fila cargada aca
respeta exactamente el mismo dedup por fecha y la misma integridad de
evidencia por SHA-256 -- no hay un camino paralelo de insercion.

Orden deliberado: primero se piden todos los datos de la cotizacion y se
intenta insertar la observacion (que puede omitirse por dedup); recien
DESPUES de confirmar que se inserto de verdad se pide la ruta de la captura
de pantalla. Esto evita el problema que tienen los colectores automatizados
en CI (ver revision del workflow): ahi la captura se toma ANTES de saber si
la observacion es duplicada, así que un rerun el mismo dia deja archivos de
evidencia huerfanos (sin fila en `evidence`) igual comiteados al repo. Aca,
si la fila es duplicada, nunca se llega a copiar ni hashear ningun archivo.

El checklist anti-promocion es una confirmacion explicita (no un comentario
que se pueda ignorar sin querer): el script no continua hasta que se
confirma que se reviso.

Uso:
    python collectors/manual_entry.py [ruta_a_remesas.db] [--operator MoneyGram]
"""

import argparse
import hashlib
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from utils import (
    DEFAULT_DB_PATH,
    EVIDENCE_DIR,
    get_corridor_id,
    get_operator_id,
    insert_evidence,
    insert_observation_if_new,
)

CORRIDORS = {
    "1": ("US", "SV", "US->SV"),
    "2": ("CA", "SV", "CA->SV"),
}


def prompt(label: str, default: str = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        if not raw and not required:
            return None
        if raw:
            return raw
        print("  (obligatorio)")


def prompt_float(label: str, default: float = None, required: bool = True) -> float:
    default_str = None if default is None else str(default)
    while True:
        raw = prompt(label, default=default_str, required=required)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            print("  numero invalido, intenta de nuevo")


def prompt_yes_no(label: str, default: bool = False) -> bool:
    default_label = "S/n" if default else "s/N"
    raw = input(f"{label} ({default_label}): ").strip().lower()
    if not raw:
        return default
    return raw in ("s", "si", "sí", "y", "yes")


def choose_corridor() -> tuple:
    print("Corredor:")
    for key, (_, _, label) in CORRIDORS.items():
        print(f"  {key}) {label}")
    while True:
        choice = input("Elegi 1 o 2: ").strip()
        if choice in CORRIDORS:
            origin, destination, _ = CORRIDORS[choice]
            return origin, destination
        print("  opcion invalida")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db_path", nargs="?", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--operator", default="MoneyGram",
        help="Nombre exacto del operador en la tabla operators (default: MoneyGram)",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        sys.exit(f"Error: no existe la base de datos en {db_path}. Corre schema/init_db.py primero.")

    EVIDENCE_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        operator_id = get_operator_id(conn, args.operator)
    except LookupError as exc:
        conn.close()
        sys.exit(f"Error: {exc}")

    print(f"=== Registro manual -- {args.operator} ===\n")
    print(
        "Checklist anti-promocion antes de seguir:\n"
        "  1. Cotizador abierto SIN sesion iniciada / en ventana privada.\n"
        "  2. Revisaste que no haya lenguaje de 'first transfer'/'new customer'/'welcome'\n"
        "     ni un precio tachado junto al que vas a cargar.\n"
        "  3. El fee/tasa que vas a cargar es el de LISTA (recurrente) -- salvo que marques\n"
        "     esta fila como promocional a proposito mas abajo.\n"
    )
    if not prompt_yes_no("Confirmas que revisaste el checklist de arriba", default=False):
        conn.close()
        sys.exit("Cancelado: revisa el checklist antes de cargar una observacion.")

    origin, destination = choose_corridor()
    try:
        corridor_id = get_corridor_id(conn, origin, destination)
    except LookupError as exc:
        conn.close()
        sys.exit(f"Error: {exc}")

    amount = prompt_float("Monto enviado (send_amount)")
    funding_method = prompt(
        "Metodo de pago (funding_method, ej. debit_card/bank_account; Enter = ninguno)", required=False
    )
    delivery_method = prompt(
        "Metodo de entrega (delivery_method, ej. cash_pickup/bank_deposit; Enter = ninguno)", required=False
    )
    fee = prompt_float("Fee/comision (tarifa de LISTA)")

    default_rate = 1.0 if origin == "US" else None
    exchange_rate = prompt_float("Tipo de cambio aplicado (exchange_rate_applied)", default=default_rate)

    is_promo = prompt_yes_no("Es esta una tarifa PROMOCIONAL (primer envio/nuevo cliente)", default=False)

    receive_amount = round((amount - fee) * exchange_rate, 2)
    total_cost_pct = round(fee / amount * 100, 4)
    print(f"\n  -> receive_amount calculado: {receive_amount}")
    print(f"  -> total_cost_pct calculado: {total_cost_pct}%\n")

    default_url = None
    row = conn.execute("SELECT website FROM operators WHERE operator_id = ?", (operator_id,)).fetchone()
    if row:
        default_url = row[0]
    source_url = prompt("URL del cotizador donde se vio esta tarifa (source_url)", default=default_url)

    notes = prompt("Notas (opcional, ej. detalle de lo que se vio en pantalla)", required=False)
    if is_promo:
        promo_note = "MARCADO PROMOCIONAL a proposito (ver checklist); no usar para comparaciones de costo real."
        notes = f"{notes} -- {promo_note}" if notes else promo_note

    timestamp = datetime.now(timezone.utc)
    observation = {
        "timestamp_utc": timestamp.isoformat(),
        "corridor_id": corridor_id,
        "operator_id": operator_id,
        "send_amount": amount,
        "funding_method": funding_method,
        "delivery_method": delivery_method,
        "fee": fee,
        "exchange_rate_applied": exchange_rate,
        "receive_amount": receive_amount,
        "total_cost_pct": total_cost_pct,
        "is_promotional": 1 if is_promo else 0,
        "collection_method": "manual",
        "source_url": source_url,
        "collector_notes": notes,
        # Hardcodeado (NO get_run_type()): este script corre localmente, nunca
        # dentro de GitHub Actions, asi que GITHUB_EVENT_NAME jamas esta
        # seteado -- get_run_type() lo clasificaria siempre como
        # 'manual_test' y lo excluiria del export publico, pese a ser el
        # metodo de recoleccion de PRODUCCION legitimo para operadores con
        # requires_manual_sampling=1 (hoy: MoneyGram). Ver docs/pitch/
        # schema-notes.md, seccion 7.3.
        "run_type": "scheduled",
    }

    observation_id = insert_observation_if_new(conn, observation)
    if observation_id is None:
        conn.commit()
        conn.close()
        print(
            "\nNo se inserto nada: ya hay una observacion equivalente cargada hoy para este "
            "operador/corredor/monto/metodos."
        )
        return

    screenshot_path_raw = prompt("Ruta a la captura que ya tomaste a mano (screenshot del cotizador)")
    screenshot_path = Path(screenshot_path_raw).expanduser()
    if not screenshot_path.is_file():
        conn.rollback()
        conn.close()
        sys.exit(f"Error: no existe el archivo {screenshot_path} -- la observacion NO se guardo (sin evidencia, no se inserta).")

    operator_slug = args.operator.lower().replace(" ", "_")
    corridor_code = f"{origin.lower()}_{destination.lower()}"
    ext = screenshot_path.suffix or ".png"
    evidence_filename = f"{operator_slug}_{corridor_code}_{int(amount)}_{timestamp.strftime('%Y%m%dT%H%M%SZ')}{ext}"
    evidence_dest = EVIDENCE_DIR / evidence_filename
    shutil.copy2(screenshot_path, evidence_dest)

    sha256_hash = hashlib.sha256(evidence_dest.read_bytes()).hexdigest()
    evidence_rel_path = f"evidence/{evidence_filename}"
    insert_evidence(conn, observation_id, evidence_rel_path, sha256_hash, timestamp.isoformat())
    conn.commit()
    conn.close()

    print(f"\nOK -- observation_id={observation_id}")
    print(f"Evidencia copiada a: {evidence_rel_path}")
    print(f"sha256: {sha256_hash}")


if __name__ == "__main__":
    main()
