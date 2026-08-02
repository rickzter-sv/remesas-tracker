"""Colector de tarifas de Remitly (US->SV y CA->SV) desde la tabla estática de precios.

No usa el calculador JS de Remitly ni Playwright: descarga el HTML crudo con
requests y parsea la tabla de tarifas por método de entrega que ya viene
renderizada en el HTML (confirmado: Remitly sirve esta tabla sin JavaScript).

Nota sobre el tipo de cambio en CA->SV: la tabla visible de tarifas de esa
página no incluye tipo de cambio, y el único que aparece en texto plano es la
"Welcome rate" promocional (solo clientes nuevos), que por la regla
anti-promoción no se usa. En vez de eso, el tipo de cambio "everyday" (no
promocional) se extrae de un JSON de hidratación que Remitly incrusta en el
propio HTML (variable __REMITLY_LANDING_PAGE_CONTEXT__) -- sigue sin requerir
ejecutar JavaScript, solo decodificar un bloque JSON que ya viene en el
documento. Ver extract_hydration_context() y get_ca_everyday_rate() para el
detalle exacto de qué clave se lee y por qué.

Uso:
    python collectors/remitly.py [ruta_a_remesas.db]
    python collectors/remitly.py --backfill-ca-rate [ruta_a_remesas.db]
        Completa exchange_rate_applied/receive_amount/total_cost_pct de filas
        CA->SV que hayan quedado en NULL, reutilizando el HTML de evidencia ya
        guardado en disco (sin descargar de nuevo ni insertar filas nuevas).
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from utils import (
    DEFAULT_DB_PATH,
    EVIDENCE_DIR,
    get_corridor_id,
    get_operator_id,
    insert_evidence,
    insert_observation_if_new,
)

OPERATOR_NAME = "Remitly"
AMOUNTS = (100, 200, 500)
TIER_THRESHOLD = 500  # "Below $500" vs "$500 or more" en la tabla de tarifas US

HYDRATION_VAR_NAME = "__REMITLY_LANDING_PAGE_CONTEXT__"
CA_RATE_TIER_THRESHOLD = 500  # ver docstring de get_ca_everyday_rate()

CORRIDORS = [
    {
        "code": "us",
        "origin_country": "US",
        "destination_country": "SV",
        "pricing_url": "https://www.remitly.com/us/en/el-salvador/pricing",
        "exchange_rate": 1.0,  # US->SV: USD->USD, sin conversion de divisa
    },
    {
        "code": "ca",
        "origin_country": "CA",
        "destination_country": "SV",
        "pricing_url": "https://www.remitly.com/ca/en/el-salvador/pricing",
        # sin "exchange_rate" fijo: se calcula por monto via get_ca_everyday_rate()
    },
]

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

NO_CA_RATE_NOTE = (
    "Tipo de cambio no disponible: la tabla estatica de esta pagina no expone "
    "una tasa no promocional en texto plano, solo la 'Welcome rate' (tasa de "
    "bienvenida para clientes nuevos). Por la regla anti-promocion no se usa "
    "esa tasa; receive_amount y total_cost_pct quedan sin calcular."
)

CA_HYDRATION_RATE_NOTE = (
    "Tipo de cambio 'everyday' (no promocional, aplica a todos los clientes) "
    "obtenido del JSON de hidratacion embebido en el HTML del sitio (variable "
    "{var_name}, clave context.merchandisingFacts.{key} = {value}), NO de la "
    "tabla visible de tarifas (esa tabla no trae tipo de cambio para este "
    "corredor) y NO de la 'Welcome rate' promocional para clientes nuevos. Ver "
    "docstring de get_ca_everyday_rate() en collectors/remitly.py para el "
    "detalle de como se localiza esta clave y por que se usa el umbral de "
    "CAD {threshold}."
)


def fetch_html(url: str) -> bytes:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    response.raise_for_status()
    return response.content


def save_evidence(corridor_code: str, html_bytes: bytes, timestamp: datetime) -> tuple[str, str]:
    filename = f"remitly_{corridor_code}_{timestamp.strftime('%Y%m%dT%H%M%SZ')}.html"
    path = EVIDENCE_DIR / filename
    path.write_bytes(html_bytes)
    sha256_hash = hashlib.sha256(html_bytes).hexdigest()
    return f"evidence/{filename}", sha256_hash


def normalize_delivery_method(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def parse_tiered_cell(text: str) -> dict:
    pct_match = re.search(r"([\d.]+)\s*%", text)
    fixed_match = re.search(r"\$\s*([\d.]+)", text)
    if not pct_match or not fixed_match:
        raise ValueError(f"No se pudo parsear celda de tarifa escalonada: {text!r}")
    return {"pct": float(pct_match.group(1)), "fixed": float(fixed_match.group(1))}


def parse_flat_fee_cell(text: str) -> float:
    cleaned = text.strip().lower()
    if cleaned in ("free", "$0", "$0.00", "0"):
        return 0.0
    match = re.search(r"\$\s*([\d.]+)", text)
    if not match:
        raise ValueError(f"No se pudo parsear celda de tarifa fija: {text!r}")
    return float(match.group(1))


def parse_fee_table(soup: BeautifulSoup) -> dict:
    table = soup.find("table")
    if table is None:
        raise ValueError("No se encontro tabla de tarifas en el HTML")

    header_cells = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    rows = table.find("tbody").find_all("tr")

    data = {}
    if len(header_cells) == 3 and "Below" in header_cells[1]:
        for row in rows:
            cells = row.find_all("td")
            method = cells[0].get_text(strip=True)
            data[method] = {
                "tiered": True,
                "below_threshold": parse_tiered_cell(cells[1].get_text(" ", strip=True)),
                "at_or_above_threshold": parse_tiered_cell(cells[2].get_text(" ", strip=True)),
            }
    elif len(header_cells) == 2 and header_cells[1].strip().lower() == "fee":
        for row in rows:
            cells = row.find_all("td")
            method = cells[0].get_text(strip=True)
            data[method] = {
                "tiered": False,
                "flat_fee": parse_flat_fee_cell(cells[1].get_text(strip=True)),
            }
    else:
        raise ValueError(f"Formato de tabla de tarifas no reconocido, headers={header_cells}")

    return data


def compute_fee(method_data: dict, amount: float) -> float:
    if method_data["tiered"]:
        tier = (
            method_data["below_threshold"]
            if amount < TIER_THRESHOLD
            else method_data["at_or_above_threshold"]
        )
        return round(tier["pct"] / 100 * amount + tier["fixed"], 2)
    return method_data["flat_fee"]


def extract_hydration_context(html_text: str) -> dict:
    """Extrae el JSON de hidratacion que Remitly incrusta inline en el HTML.

    En el HTML servido (sin ejecutar JS) hay un <script> con una asignacion
    literal de la forma:

        __REMITLY_LANDING_PAGE_CONTEXT__ = { ... JSON valido ... };

    Esto es texto plano ya presente en la respuesta de `requests.get()`, no
    requiere un navegador. Se ubica el marcador por nombre de variable y se
    decodifica el objeto JSON que le sigue con json.JSONDecoder().raw_decode,
    que ignora el ";" y cualquier cosa despues del cierre del objeto.

    Si Remitly renombra esta variable o deja de incrustar el JSON como texto
    plano (p. ej. si pasan todo a fetch client-side), esta funcion debe
    revisarse: por eso levanta ValueError explicito en vez de fallar en
    silencio o devolver un dict vacio.
    """
    marker = f"{HYDRATION_VAR_NAME} = "
    idx = html_text.find(marker)
    if idx == -1:
        raise ValueError(
            f"No se encontro la variable {HYDRATION_VAR_NAME!r} en el HTML; "
            "es posible que Remitly haya cambiado el formato de la pagina."
        )
    start = idx + len(marker)
    try:
        context, _ = json.JSONDecoder().raw_decode(html_text, start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"El bloque de hidratacion no es JSON valido: {exc}") from exc
    return context


def get_ca_everyday_rate(hydration_context: dict, amount: float) -> tuple[float, str]:
    """Lee el tipo de cambio CAD->USD "everyday" (no promocional) del JSON de
    hidratacion, y devuelve (tasa, nombre_de_la_clave_usada).

    Ruta dentro del JSON: context.merchandisingFacts.<clave>, donde <clave> es
    una de:
      - everydayRateAsLowAs  (p.ej. "0.6887"): el extremo mas bajo del rango
        de tasa everyday -- se asume que aplica a montos pequenos.
      - everydayRateAsHighAs (p.ej. "0.6999"): el extremo mas alto del rango
        (mejor tasa) -- se asume que aplica a montos grandes.

    El JSON no etiqueta explicitamente en que monto ocurre el corte entre
    ambas claves. Se usa el umbral CA_RATE_TIER_THRESHOLD (500 CAD) porque:
      (a) es el mismo umbral que Remitly usa para su oferta promocional
          ("Promotional FX rate applies to first CAD 500.00 sent"), y
      (b) se verifico manualmente el 2026-08-01 simulando un envio de 1,000
          CAD en el calculador de la pagina: el texto muestra "Standard rate
          1 CAD = 0.6999 USD applies to the rest of the transfer" para los
          500 CAD que exceden el umbral promocional, y ese 0.6999 coincide
          exactamente con everydayRateAsHighAs.

    Si Remitly cambia este umbral, renombra estas claves, o deja de exponer
    merchandisingFacts, esta funcion debe revisarse -- levanta KeyError
    explicito en vez de asumir un valor por defecto.
    """
    facts = hydration_context.get("context", {}).get("merchandisingFacts")
    if not facts:
        raise KeyError("No se encontro context.merchandisingFacts en el JSON de hidratacion")

    key = "everydayRateAsLowAs" if amount < CA_RATE_TIER_THRESHOLD else "everydayRateAsHighAs"
    if key not in facts:
        raise KeyError(f"La clave {key!r} no existe en merchandisingFacts: {list(facts.keys())}")

    return float(facts[key]), key


def collect_corridor(conn: sqlite3.Connection, corridor: dict) -> list[int]:
    html_bytes = fetch_html(corridor["pricing_url"])
    timestamp = datetime.now(timezone.utc)
    evidence_path, sha256_hash = save_evidence(corridor["code"], html_bytes, timestamp)

    soup = BeautifulSoup(html_bytes, "html.parser")
    fee_table = parse_fee_table(soup)

    corridor_id = get_corridor_id(conn, corridor["origin_country"], corridor["destination_country"])
    operator_id = get_operator_id(conn, OPERATOR_NAME)

    hydration_context = None
    hydration_error = None
    if corridor["code"] == "ca":
        try:
            hydration_context = extract_hydration_context(html_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            hydration_error = str(exc)

    inserted_ids = []
    for delivery_method_raw, method_data in fee_table.items():
        for amount in AMOUNTS:
            fee = compute_fee(method_data, amount)

            is_promotional = 0
            fee_note = None
            if fee <= 0:
                is_promotional = 1
                fee_note = (
                    "ADVERTENCIA: la tarifa parseada de la tabla fue $0.00, lo cual "
                    "coincide con el patron de descuento promocional de primer envio. "
                    "No se usa como tarifa real; revisar manualmente antes de confiar "
                    "en esta fila."
                )

            exchange_rate = None
            rate_note = None
            if corridor["code"] == "us":
                exchange_rate = corridor["exchange_rate"]
            elif corridor["code"] == "ca":
                if hydration_context is not None:
                    try:
                        exchange_rate, rate_key = get_ca_everyday_rate(hydration_context, amount)
                        rate_note = CA_HYDRATION_RATE_NOTE.format(
                            var_name=HYDRATION_VAR_NAME,
                            key=rate_key,
                            value=exchange_rate,
                            threshold=CA_RATE_TIER_THRESHOLD,
                        )
                    except KeyError as exc:
                        rate_note = f"No se pudo leer el tipo de cambio del JSON de hidratacion ({exc}). {NO_CA_RATE_NOTE}"
                else:
                    rate_note = f"No se pudo parsear el JSON de hidratacion embebido ({hydration_error}). {NO_CA_RATE_NOTE}"

            if exchange_rate is not None:
                receive_amount = round((amount - fee) * exchange_rate, 2)
                total_cost_pct = round(fee / amount * 100, 4)
            else:
                receive_amount = None
                total_cost_pct = None

            notes = " ".join(n for n in (fee_note, rate_note) if n) or None

            observation = {
                "timestamp_utc": timestamp.isoformat(),
                "corridor_id": corridor_id,
                "operator_id": operator_id,
                "send_amount": amount,
                "funding_method": None,
                "delivery_method": normalize_delivery_method(delivery_method_raw),
                "fee": fee,
                "exchange_rate_applied": exchange_rate,
                "receive_amount": receive_amount,
                "total_cost_pct": total_cost_pct,
                "is_promotional": is_promotional,
                "collection_method": "automated",
                "source_url": corridor["pricing_url"],
                "collector_notes": notes,
            }

            observation_id = insert_observation_if_new(conn, observation)
            if observation_id is None:
                continue
            insert_evidence(conn, observation_id, evidence_path, sha256_hash, timestamp.isoformat())
            inserted_ids.append(observation_id)

    conn.commit()
    print(
        f"{corridor['code'].upper()}->SV: {len(inserted_ids)} observaciones insertadas "
        f"desde {corridor['pricing_url']}"
    )
    print(f"  Evidencia: {evidence_path} (sha256={sha256_hash})")
    return inserted_ids


def backfill_ca_exchange_rates(conn: sqlite3.Connection) -> list[int]:
    """Completa exchange_rate_applied/receive_amount/total_cost_pct de filas
    CA->SV que hayan quedado en NULL (p.ej. de una corrida anterior a que
    existiera get_ca_everyday_rate()).

    No vuelve a descargar nada: reutiliza el HTML de evidencia ya guardado en
    disco para cada fila (evidence.file_path), para que la tasa que se guarda
    corresponda exactamente al mismo HTML ya referenciado como evidencia de
    esa observacion, en vez de mezclar datos de una descarga nueva.

    Selecciona las filas por corridor_id + operator_id + exchange_rate_applied
    IS NULL, no por timestamp: es el criterio mas robusto porque identifica
    exactamente las filas que faltan por completar sin importar de que
    corrida vinieron, y hace que la funcion sea segura de re-ejecutar (una
    fila ya completada deja de matchear el filtro, así que nunca se
    actualiza dos veces ni se duplica).
    """
    corridor_id = get_corridor_id(conn, "CA", "SV")
    operator_id = get_operator_id(conn, OPERATOR_NAME)

    rows = conn.execute(
        """
        SELECT o.observation_id, o.send_amount, o.fee, e.file_path
        FROM observations o
        JOIN evidence e ON e.observation_id = o.observation_id
        WHERE o.corridor_id = ? AND o.operator_id = ? AND o.exchange_rate_applied IS NULL
        """,
        (corridor_id, operator_id),
    ).fetchall()

    if not rows:
        print("CA->SV: no hay filas pendientes de backfill (exchange_rate_applied ya resuelto en todas).")
        return []

    context_cache: dict = {}
    updated_ids = []
    for observation_id, send_amount, fee, file_path in rows:
        if file_path not in context_cache:
            html_text = (REPO_ROOT / file_path).read_text(encoding="utf-8")
            context_cache[file_path] = extract_hydration_context(html_text)
        hydration_context = context_cache[file_path]

        exchange_rate, rate_key = get_ca_everyday_rate(hydration_context, send_amount)
        receive_amount = round((send_amount - fee) * exchange_rate, 2)
        total_cost_pct = round(fee / send_amount * 100, 4)
        notes = CA_HYDRATION_RATE_NOTE.format(
            var_name=HYDRATION_VAR_NAME,
            key=rate_key,
            value=exchange_rate,
            threshold=CA_RATE_TIER_THRESHOLD,
        )

        conn.execute(
            """
            UPDATE observations
            SET exchange_rate_applied = ?, receive_amount = ?, total_cost_pct = ?, collector_notes = ?
            WHERE observation_id = ?
            """,
            (exchange_rate, receive_amount, total_cost_pct, notes, observation_id),
        )
        updated_ids.append(observation_id)

    conn.commit()
    print(f"CA->SV: {len(updated_ids)} observaciones actualizadas con tipo de cambio del JSON de hidratacion.")
    return updated_ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", nargs="?", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--backfill-ca-rate",
        action="store_true",
        help=(
            "Solo completa filas CA->SV con exchange_rate_applied NULL usando "
            "el HTML de evidencia ya guardado; no descarga nada ni inserta filas."
        ),
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        sys.exit(f"Error: no existe la base de datos en {db_path}. Corre schema/init_db.py primero.")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if args.backfill_ca_rate:
            backfill_ca_exchange_rates(conn)
        else:
            EVIDENCE_DIR.mkdir(exist_ok=True)
            for corridor in CORRIDORS:
                collect_corridor(conn, corridor)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
