"""Colector de tarifas de Ria Money Transfer (US->SV y CA->SV) via Playwright.

Al igual que Western Union, Ria es una SPA: la comision solo aparece despues
de elegir destino, monto y metodo de pago en el widget de cotizacion de la
pagina de inicio. A diferencia de WU en US (que muestra los 5 metodos de
pago a la vez en un resumen), Ria muestra UN metodo de pago a la vez -- hay
que abrir el selector "Payment method" y elegir cada opcion por turno para
leer su comision.

Regla anti-promocion (dos focos distintos, confirmados contra el sitio real
el 2026-08-01, no solo asumidos):
  1. Comision: el widget siempre marca el primer envio como gratis. La cifra
     de LISTA aparece tachada junto a "Fee" (ej. "4.90"), y al lado se
     muestra "0 USD first transfer fee" (o un monto parcial en otros casos)
     como lo que se cobraria hoy. Se guarda SIEMPRE la cifra tachada en
     `fee`.
  2. Tipo de cambio (solo CA->SV, porque El Salvador esta dolarizado y por
     tanto US->SV es 1:1 sin promocion posible): el banner superior "Your
     first transfer has a promo rate!" muestra TAMBIEN dos cifras, "1 CAD =
     X.XXXXX Y.YYYYYY USD" -- X (tachada) es la tasa de lista/everyday, Y
     (resaltada) es la tasa promocional que el widget usa por defecto para
     calcular "They receive". Este colector recalcula receive_amount con la
     tasa de LISTA, nunca con la tasa promocional del banner.

Algunos metodos de pago tienen un rango de monto valido mas chico que otros
(ej. "Cash" en US solo admite hasta 495 USD): cuando el monto solicitado cae
fuera de ese rango, el widget muestra un mensaje de error ("Enter an amount
between...") en vez de una tarifa, y esa combinacion se omite (no se inserta
una fila con fee=0, que se confundiria con una promocion).

Reintentos por corredor (agregado 2026-08-03): en GitHub Actions (IP de
datacenter) el selector de destino a veces devuelve "No results." y el
widget completo muestra "Unable to get rates. Please try again." -- confirmado
reproduciendo el mismo flujo desde otra IP no residencial. No es un bloqueo
anti-bot explicito (no hay mensaje de deteccion, a diferencia de MoneyGram),
sino que parece una falla intermitente del backend de tasas de Ria bajo IPs
no residenciales. collect_corridor() completo (no solo el paso que fallo) se
reintenta hasta RETRY_ATTEMPTS veces con una pausa corta entre intentos: es
seguro repetirlo porque insert_observation_if_new ya deduplica por dia, asi
que un reintento que repite un monto/metodo ya insertado en un intento
anterior simplemente lo omite, no lo duplica. Si se agotan los intentos, se
relanza la ultima excepcion tal cual -- run_all.py ya aisla el fallo por
colector sin bloquear a los demas, asi que no hace falta absorberlo aca.

Uso:
    python collectors/ria.py [ruta_a_remesas.db]
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from utils import (
    DEFAULT_DB_PATH,
    EVIDENCE_DIR,
    REQUEST_USER_AGENT,
    capture_screenshot_evidence,
    dismiss_cookie_banner,
    get_corridor_id,
    get_operator_id,
    insert_evidence,
    insert_observation_if_new,
    is_promotional,
)

OPERATOR_NAME = "Ria Money Transfer"
OPERATOR_SLUG = "ria"
AMOUNTS = (100, 200, 500)

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (5, 10)

US_URL = "https://www.riamoneytransfer.com/en-us"
CA_URL = "https://www.riamoneytransfer.com/en-ca"

DELIVERY_METHOD_LABEL = "Bank"  # fijo para ambos corredores, igual que en western_union.py

US_PAYMENT_METHODS = [
    ("Bank", "bank_account"),
    ("Credit card", "credit_card"),
    ("Debit card", "debit_card"),
    ("Cash", "cash"),
]
CA_PAYMENT_METHODS = [
    ("Credit card", "credit_card"),
    ("Debit card", "debit_card"),
]

COOKIE_BANNER_LABELS = ("Accept", "Accept All", "I Accept", "Got it")

FEE_PATTERN = re.compile(
    r"Fee\s+([\d.]+)(?:\s+([\d.]+)\s*(?:USD|CAD)\s*first transfer fee)?\s*Total to pay"
)

# OJO: "Enter an amount between X-Y" es un hint estatico que el widget
# muestra SIEMPRE (incluso con una cotizacion valida) -- no es un indicador
# de error. La cotizacion realmente invalida solo agrega "Maximum is ..." o
# "Minimum is ..." y en ese caso NO hay seccion "Fee" en absoluto.
OUT_OF_RANGE_PATTERN = re.compile(r"Maximum is|Minimum is", re.IGNORECASE)
CA_RATE_PROMO_PATTERN = re.compile(r"1 CAD =\s*([\d.]+)\s+([\d.]+)\s*USD")
CA_RATE_PLAIN_PATTERN = re.compile(r"1 CAD =\s*([\d.]+)\s*USD")


def get_quote_text(page: Page) -> str:
    return page.locator("text=Quote details").locator("xpath=..").inner_text(timeout=10000)


def select_destination(page: Page, country: str = "El Salvador") -> None:
    page.get_by_role("button", name="Select Destination").click(timeout=10000)
    page.wait_for_timeout(400)
    page.get_by_placeholder("Search currency or country").fill(country)
    page.wait_for_timeout(500)
    page.get_by_role("option").first.click(timeout=10000)
    page.wait_for_timeout(1000)


def select_dropdown_option(page: Page, label_text: str, option_name: str) -> None:
    """Abre el selector (Payment method / Delivery method) que sigue al label
    dado y elige la opcion `option_name`. Ambos selectores en Ria son
    dialogs modales sin aria-label propio, asi que se ubican por el texto
    del label vecino (confirmado contra el DOM real, ver docstring del
    modulo).

    exact=False a proposito: las opciones de "Delivery method" incluyen un
    subtexto con el tipo de cambio pegado al nombre (ej. "Bank\\n1 USD =
    1.000000 USD"), asi que el nombre accesible completo nunca matchea
    exact=True aunque el texto visible sea solo "Bank". Las opciones de
    "Payment method" no tienen ese subtexto y no colisionan entre si, asi
    que el match parcial es seguro para ambos selectores.
    """
    trigger = page.get_by_text(label_text, exact=True).locator("xpath=following-sibling::*[1]")
    trigger.click(timeout=10000)
    page.wait_for_timeout(400)
    page.get_by_role("option", name=option_name, exact=False).first.click(timeout=10000)
    page.wait_for_timeout(1000)


def set_amount(page: Page, amount: int) -> None:
    field = page.get_by_label("You send")
    field.click(timeout=10000)
    field.fill("")
    field.fill(str(amount))
    field.blur()
    page.wait_for_timeout(1200)


def parse_fee(text: str) -> Optional[tuple[float, float, bool]]:
    """Devuelve (fee_lista, fee_cobrado, is_promo), o None si el monto esta
    fuera del rango valido para el metodo de pago actual (ver docstring del
    modulo)."""
    if OUT_OF_RANGE_PATTERN.search(text):
        return None
    match = FEE_PATTERN.search(text)
    if not match:
        raise ValueError(f"No se pudo leer 'Fee' en el resumen de Ria: {text!r}")
    fee_list = float(match.group(1))
    if match.group(2) is not None:
        fee_actual = float(match.group(2))
    else:
        fee_actual = fee_list
    return fee_list, fee_actual, is_promotional(fee_list, fee_actual)


def parse_ca_rate(text: str) -> tuple[float, float, bool]:
    """Devuelve (tasa_lista, tasa_promocional_o_igual, is_promo) leyendo el
    banner "1 CAD = X Y USD" (ver docstring del modulo). Si no hay banner de
    promocion (solo aparece un numero), devuelve la misma tasa dos veces."""
    promo_match = CA_RATE_PROMO_PATTERN.search(text)
    if promo_match:
        rate_list = float(promo_match.group(1))
        rate_promo = float(promo_match.group(2))
        return rate_list, rate_promo, is_promotional(rate_list, rate_promo)
    plain_match = CA_RATE_PLAIN_PATTERN.search(text)
    if not plain_match:
        raise ValueError(f"No se pudo leer la tasa '1 CAD = ... USD' en: {text!r}")
    rate = float(plain_match.group(1))
    return rate, rate, False


def collect_corridor(
    page: Page,
    conn: sqlite3.Connection,
    *,
    url: str,
    corridor_code: str,
    corridor_id: int,
    operator_id: int,
    currency: str,
    payment_methods: list,
    parse_rate: bool,
) -> list[int]:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    dismiss_cookie_banner(page, labels=COOKIE_BANNER_LABELS)
    page.wait_for_timeout(500)
    select_destination(page)
    select_dropdown_option(page, "Delivery method", DELIVERY_METHOD_LABEL)

    inserted_ids = []
    for amount in AMOUNTS:
        set_amount(page, amount)

        for payment_label, funding_method in payment_methods:
            select_dropdown_option(page, "Payment method", payment_label)

            text = get_quote_text(page)

            if parse_rate:
                rate_list, rate_promo, rate_is_promo = parse_ca_rate(text)
            else:
                rate_list, rate_promo, rate_is_promo = 1.0, 1.0, False

            fee_result = parse_fee(text)
            if fee_result is None:
                print(
                    f"{corridor_code.upper()}->SV ${amount} ({payment_label}): fuera de rango valido, "
                    f"se omite (no se inserta fila)."
                )
                continue
            fee_list, fee_actual, fee_is_promo = fee_result

            timestamp = datetime.now(timezone.utc)
            evidence_path, sha256_hash = capture_screenshot_evidence(
                page, OPERATOR_SLUG, f"{corridor_code}_{funding_method}", amount, timestamp
            )

            receive_amount = round((amount - fee_list) * rate_list, 2)
            total_cost_pct = round(fee_list / amount * 100, 4)
            is_promo = fee_is_promo or rate_is_promo

            notes_parts = []
            if fee_is_promo:
                notes_parts.append(
                    f"Tarifa de lista (real/recurrente) = {fee_list:.2f} {currency}, leida tachada junto a "
                    f"'Fee' en el widget de cotizacion de Ria. El sitio muestra actualmente un descuento "
                    f"promocional de primer envio que cobra {fee_actual:.2f} {currency} en su lugar; ese "
                    f"valor promocional NO se usa como fee ni para receive_amount/total_cost_pct."
                )
            if rate_is_promo:
                notes_parts.append(
                    f"Tipo de cambio de lista (everyday) = {rate_list:.6f} USD por CAD, leido tachado en el "
                    f"banner '1 CAD = ...' del widget. El sitio muestra actualmente una tasa promocional de "
                    f"primer envio de {rate_promo:.6f} USD por CAD; esa tasa promocional NO se usa para "
                    f"receive_amount/total_cost_pct."
                )
            notes = " ".join(notes_parts) or None

            observation = {
                "timestamp_utc": timestamp.isoformat(),
                "corridor_id": corridor_id,
                "operator_id": operator_id,
                "send_amount": amount,
                "funding_method": funding_method,
                "delivery_method": "bank_account",
                "fee": fee_list,
                "exchange_rate_applied": rate_list,
                "receive_amount": receive_amount,
                "total_cost_pct": total_cost_pct,
                "is_promotional": int(is_promo),
                "collection_method": "automated",
                "source_url": url,
                "collector_notes": notes,
            }
            observation_id = insert_observation_if_new(conn, observation)
            if observation_id is None:
                conn.commit()
                continue
            insert_evidence(conn, observation_id, evidence_path, sha256_hash, timestamp.isoformat())
            inserted_ids.append(observation_id)
            conn.commit()
            print(
                f"{corridor_code.upper()}->SV ${amount} ({payment_label}): 1 observacion insertada. "
                f"Evidencia: {evidence_path} (sha256={sha256_hash})"
            )

    return inserted_ids


def collect_corridor_with_retries(page: Page, conn: sqlite3.Connection, **kwargs) -> list[int]:
    """Reintenta collect_corridor() completo ante cualquier excepcion, con
    una pausa corta entre intentos (ver docstring del modulo para el porque).
    Relanza la ultima excepcion tal cual si se agotan los intentos."""
    corridor_code = kwargs.get("corridor_code", "?")
    last_exc = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return collect_corridor(page, conn, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt == RETRY_ATTEMPTS:
                break
            backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            print(
                f"Intento {attempt}/{RETRY_ATTEMPTS} fallo para corredor {corridor_code.upper()}->SV: "
                f"{type(exc).__name__}: {exc}. Reintentando en {backoff}s..."
            )
            page.wait_for_timeout(backoff * 1000)
    raise last_exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", nargs="?", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--headed", action="store_true", help="Corre el navegador visible (debug).")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        sys.exit(f"Error: no existe la base de datos en {db_path}. Corre schema/init_db.py primero.")

    EVIDENCE_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        us_corridor_id = get_corridor_id(conn, "US", "SV")
        ca_corridor_id = get_corridor_id(conn, "CA", "SV")
        operator_id = get_operator_id(conn, OPERATOR_NAME)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(user_agent=REQUEST_USER_AGENT, viewport={"width": 1400, "height": 1000})
            try:
                collect_corridor_with_retries(
                    page,
                    conn,
                    url=US_URL,
                    corridor_code="us",
                    corridor_id=us_corridor_id,
                    operator_id=operator_id,
                    currency="USD",
                    payment_methods=US_PAYMENT_METHODS,
                    parse_rate=False,
                )
                collect_corridor_with_retries(
                    page,
                    conn,
                    url=CA_URL,
                    corridor_code="ca",
                    corridor_id=ca_corridor_id,
                    operator_id=operator_id,
                    currency="CAD",
                    payment_methods=CA_PAYMENT_METHODS,
                    parse_rate=True,
                )
            finally:
                browser.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
