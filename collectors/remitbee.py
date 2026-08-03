"""Colector de tarifas de RemitBee (CA->SV unicamente) via Playwright.

RemitBee solo cubre el corredor CA->SV (no opera en US, ver operators.notes
y schema.sql). La calculadora publica y anonima de
https://www.remitbee.com/send-money/el-salvador expone:
  - Un monto de envio (input #sendingEnd) y el tipo de cambio CAD->USD.
  - Un selector "Pay with" que, al abrirse, muestra las 4 tarifas (una por
    metodo de pago: Debit, E-Transfer, Bill Payment, Direct Withdrawal/EFT)
    de una sola vez -- no hay que seleccionar cada una por turno.
No expone selector de metodo de ENTREGA: el texto de la pagina (tabla
"Transparent Money Transfer Fees...") solo menciona "bank account" para
este corredor, nunca "cash pickup" ni "bank deposit" como alternativas, asi
que se asume (y se documenta) delivery_method='bank_account' fijo.

Regla anti-promocion -- dos verificaciones especificas pedidas antes de
construir este colector (no asumidas):
  1. La pagina anuncia "Your very first transfer is completely free" (ver
     schema.sql), pero esa frase vive en un parrafo de marketing, NO en la
     calculadora: la calculadora anonima (sin sesion/cuenta) no tiene forma
     de saber si el usuario ya envio antes, asi que siempre muestra la
     tarifa ESTANDAR/recurrente (confirmado leyendo el HTML: la clase CSS
     del texto de tarifa no tiene "line-through"/tachado en ningun monto ni
     metodo de pago probado). Por eso este colector NO trata como
     promocional el valor que lee de la calculadora.
  2. El tipo de cambio (pregunta explicita: "verifica si afecta tambien el
     tipo de cambio, como en Ria"): NO. El nodo que muestra "1 CAD = X USD"
     tiene la clase CSS literal "rb-regular-rate-text" (texto de tasa
     REGULAR) y no hay tachado ni una segunda cifra "promocional" en
     ningun monto probado -- a diferencia de Ria, aqui no hay dos tasas
     compitiendo, solo una.
  3. El fee $0.00 ("Free") que aparece para E-Transfer/Bill Payment/EFT en
     montos >= $500 CAD tampoco es la promocion de "primer envio": el texto
     de la pagina lo describe como una condicion PERMANENTE ("you can
     ALWAYS transfer $500 or more at zero cost..."), sin tachado ni mencion
     de "primer envio"/"nuevo cliente". Se guarda con is_promotional=0 y
     una nota aclaratoria, igual que el caso de PYUSD en collectors/xoom.py.
Aun asi, check_no_promo_markers() revisa cada corrida por si RemitBee
cambia esto en el futuro (tachado o lenguaje de "first transfer"/"new
customer" en el propio dropdown de tarifas), en vez de asumir en silencio
que seguira igual.

Uso:
    python collectors/remitbee.py [ruta_a_remesas.db]
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

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
)

OPERATOR_NAME = "RemitBee"
OPERATOR_SLUG = "remitbee"
AMOUNTS = (100, 200, 500)

URL = "https://www.remitbee.com/send-money/el-salvador"
DELIVERY_METHOD = "bank_account"  # unico metodo mencionado para este corredor; sin selector en la UI

FUNDING_METHOD_MAP = {
    "Debit": "debit_card",
    "E-Transfer": "e_transfer",
    "Bill Payment": "bill_payment",
    "Direct Withdrawal (EFT)": "eft",
}

PROMO_KEYWORDS = ("first transfer", "new customer", "welcome rate", "welcome offer")

FREE_TIER_NOTE = (
    "Fee=0.00 CAD para este metodo de pago en este monto. La pagina de RemitBee describe esto como "
    "una condicion PERMANENTE ('you can always transfer $500 or more at zero cost when you pay "
    "through e-transfer, EFT, or bill payment'), no como la promocion de 'primer envio gratis' "
    "anunciada por separado en otra seccion de la pagina -- esa promocion depende de tener cuenta/ "
    "sesion, algo que la calculadora publica y anonima no puede reflejar. Por eso NO se marca "
    "is_promotional=1 aqui; ver docstring de collectors/remitbee.py para el detalle completo."
)


def set_amount(page: Page, amount: int) -> None:
    """Click + limpiar antes de escribir el nuevo monto es necesario: en
    pruebas manuales, un fill() directo sobre el valor pre-cargado (sin
    click/clear previos) dejaba el campo de React en un estado inconsistente
    que NO disparaba el recalculo de tarifas -- el desplegable de "Pay with"
    seguia mostrando las tarifas del monto anterior (confirmado comparando
    ambos flujos lado a lado antes de fijar este patron).

    Ademas, incluso con click+clear, la PRIMERA vez que se cambia el monto
    tras cargar la pagina puede leerse antes de que termine el recalculo
    asincrono (confirmado: una corrida real inserto fee=8.99/Free para
    $100, que son los valores del monto por defecto $1,000, no los de
    $100). Por eso se espera explicitamente a que el campo "They receive"
    cambie de valor -- siempre cambia con el monto de envio (no depende de
    a que tarifa quedo el metodo de pago seleccionado), a diferencia de la
    tarifa mostrada, que a veces coincide entre montos distintos (ej. 2.99
    CAD tanto en $100 como en $200) y por eso no serviria como senal de
    "ya se recalculo".

    El dialogo de cookies tambien puede reaparecer entre iteraciones (ver
    open_payment_dropdown), asi que se vuelve a intentar cerrar aqui antes
    del click sobre el campo de monto."""
    dismiss_cookie_banner(page)
    receiving_field = page.locator("#receivingEnd")
    previous_value = receiving_field.input_value()

    field = page.locator("#sendingEnd")
    field.click()
    field.fill("")
    field.fill(str(amount))
    field.blur()

    try:
        page.wait_for_function(
            "([sel, prev]) => document.querySelector(sel)?.value !== prev",
            arg=["#receivingEnd", previous_value],
            timeout=8000,
        )
    except Exception as exc:
        raise ValueError(
            f"El campo 'They receive' no cambio de valor tras fijar el monto en {amount} "
            f"(seguia en {previous_value!r}); el recalculo de tarifas probablemente no se completo."
        ) from exc
    page.wait_for_timeout(500)


def get_rate(page: Page) -> float:
    text = page.inner_text("body")
    match = re.search(r"1 CAD\s+([\d.]+) USD", text)
    if not match:
        raise ValueError(f"No se pudo leer '1 CAD = ... USD' en la pagina: {text[:500]!r}")
    return float(match.group(1))


def open_payment_dropdown(page: Page):
    # El dialogo de cookies puede reaparecer/re-renderizar entre iteraciones
    # de monto (confirmado empiricamente: bloqueo el clic en la segunda
    # corrida aunque ya se habia cerrado antes), asi que se vuelve a
    # intentar cerrar aqui, de forma idempotente, antes de cada apertura.
    dismiss_cookie_banner(page)
    trigger = page.get_by_text("Pay with:", exact=False).first.locator("xpath=following-sibling::*[1]")
    trigger.click(timeout=10000)
    page.wait_for_timeout(700)
    dropdown = page.locator('[class*="rb-dropDownList-wrapper"]')
    dropdown.first.wait_for(state="visible", timeout=10000)
    return dropdown.first


def check_no_promo_markers(dropdown_html: str) -> None:
    lowered = dropdown_html.lower()
    if "line-through" in lowered or "strikethrough" in lowered:
        raise ValueError(
            "Se detecto una clase/estilo de tachado (line-through/strikethrough) en el desplegable de "
            "tarifas de RemitBee, que no estaba presente cuando se construyo este colector (2026-08-02). "
            "Esto probablemente significa que RemitBee empezo a mostrar una tarifa promocional junto a "
            "la de lista; revisar manualmente antes de confiar en los datos parseados."
        )
    for keyword in PROMO_KEYWORDS:
        if keyword in lowered:
            raise ValueError(
                f"Se detecto la palabra clave de promocion {keyword!r} dentro del desplegable de tarifas "
                "de RemitBee; revisar manualmente antes de confiar en los datos parseados."
            )


def parse_fee_dropdown(dropdown_text: str) -> dict:
    """Parsea el texto del desplegable "Pay with" en {funding_key: fee}.

    El texto es una secuencia plana alternando etiqueta/valor (ej. "Debit\\n
    8.99 CAD\\nE-Transfer\\nFree\\n..."), igual que el patron ya usado en
    collectors/xoom.py: se recorre como maquina de estados en vez de un
    regex gigante porque las etiquetas son fijas y conocidas."""
    tokens = [t.strip() for t in dropdown_text.split("\n") if t.strip()]
    fees = {}
    pending_label = None
    for tok in tokens:
        if tok in FUNDING_METHOD_MAP:
            pending_label = FUNDING_METHOD_MAP[tok]
            continue
        if pending_label is None:
            raise ValueError(f"Valor de tarifa {tok!r} sin metodo de pago asociado (parseo desalineado)")
        cleaned = tok.strip().lower()
        value = 0.0 if cleaned == "free" else float(re.sub(r"[^\d.]", "", tok))
        fees[pending_label] = value
        pending_label = None

    if len(fees) != len(FUNDING_METHOD_MAP):
        raise ValueError(f"Se esperaban {len(FUNDING_METHOD_MAP)} metodos de pago, se parsearon {len(fees)}: {fees}")
    return fees


def collect(page: Page, conn: sqlite3.Connection, corridor_id: int, operator_id: int) -> list:
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    dismiss_cookie_banner(page)
    page.wait_for_selector("#sendingEnd", timeout=30000)

    inserted_ids = []
    for amount in AMOUNTS:
        set_amount(page, amount)
        exchange_rate = get_rate(page)

        dropdown = open_payment_dropdown(page)
        dropdown_text = dropdown.inner_text(timeout=10000)
        dropdown_html = dropdown.inner_html(timeout=10000)
        check_no_promo_markers(dropdown_html)
        fees = parse_fee_dropdown(dropdown_text)

        timestamp = datetime.now(timezone.utc)
        # full_page=True dispara el re-render del dialogo de cookies de
        # RemitBee (confirmado: tapaba todo el widget incluido el
        # desplegable ya abierto). El widget y el desplegable de "Pay with"
        # caben enteros en el viewport inicial, asi que no hace falta
        # capturar la pagina completa. OJO: NO se debe volver a llamar
        # dismiss_cookie_banner() justo aqui -- se probo y el clic en
        # "Accept all" cierra el desplegable de "Pay with" como efecto
        # secundario (clic fuera de el), perdiendo la evidencia visual de
        # las 4 tarifas. El dialogo de cookies ya se intento cerrar antes de
        # abrir el desplegable (ver open_payment_dropdown); si reaparece
        # despues, se acepta que tape parcialmente la captura antes que
        # arriesgar cerrar el desplegable.
        evidence_path, sha256_hash = capture_screenshot_evidence(page, OPERATOR_SLUG, "ca", amount, timestamp, full_page=False)

        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        inserted_this_amount = 0
        for funding_method, fee in fees.items():
            receive_amount = round((amount - fee) * exchange_rate, 2)
            total_cost_pct = round(fee / amount * 100, 4)

            notes = FREE_TIER_NOTE if fee <= 0 else None

            observation = {
                "timestamp_utc": timestamp.isoformat(),
                "corridor_id": corridor_id,
                "operator_id": operator_id,
                "send_amount": amount,
                "funding_method": funding_method,
                "delivery_method": DELIVERY_METHOD,
                "fee": fee,
                "exchange_rate_applied": exchange_rate,
                "receive_amount": receive_amount,
                "total_cost_pct": total_cost_pct,
                "is_promotional": 0,
                "collection_method": "automated",
                "source_url": URL,
                "collector_notes": notes,
            }
            observation_id = insert_observation_if_new(conn, observation)
            if observation_id is None:
                continue
            insert_evidence(conn, observation_id, evidence_path, sha256_hash, timestamp.isoformat())
            inserted_ids.append(observation_id)
            inserted_this_amount += 1

        conn.commit()
        print(
            f"CA->SV ${amount}: {inserted_this_amount} observaciones insertadas. "
            f"Evidencia: {evidence_path} (sha256={sha256_hash})"
        )

    return inserted_ids


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
        corridor_id = get_corridor_id(conn, "CA", "SV")
        operator_id = get_operator_id(conn, OPERATOR_NAME)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(user_agent=REQUEST_USER_AGENT, viewport={"width": 1400, "height": 1000})
            try:
                collect(page, conn, corridor_id, operator_id)
            finally:
                browser.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
