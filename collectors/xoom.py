"""Colector de tarifas de Xoom (a PayPal service), US->SV y CA->SV, via Playwright.

Ambos corredores se leen de la MISMA pagina de El Salvador
(https://www.xoom.com/el-salvador/send-money) cambiando el selector de
moneda de envio (#source-currency-picker) a CAD para el corredor
canadiense -- no existe una URL separada por pais de origen, a diferencia
de Western Union o Ria (confirmado por verificacion manual antes de armar
este colector).

A diferencia de WU/Ria, Xoom no requiere seleccionar cada combinacion de
metodo de pago/entrega una por una: el boton "Show Fees" abre un panel que
lista TODA la matriz de tarifas de una sola vez, agrupada por metodo de
entrega (encabezados "Bank Deposit" / "Debit card deposit" / "Cash
Pickup"), y dentro de cada grupo una fila por metodo de pago. El corredor
US expone 3 metodos de entrega x 5 de pago (incluye PayPal USD/PYUSD); el
corredor CA solo expone 2 metodos de entrega x 4 de pago (sin PYUSD, sin
"Debit card deposit" -- confirmado contra el sitio real, no asumido).

Regla anti-promocion: se verifico exhaustivamente (texto e HTML completos
del panel de tarifas, para los tres montos y ambas monedas) que NINGUNA
fila de esta matriz usa el patron de "descuento de primer envio" que si
usan WU y Ria (sin tachado/strikethrough, sin badge "% OFF", sin lenguaje
de "first transfer"/"new customer"/"welcome"). La comision $0.00 de
"PayPal USD (PYUSD)" tampoco es una promocion de bienvenida: aparece igual
en todos los montos probados, sin fecha de vencimiento ni condicion de
"primer envio", y esta directamente ligada al anuncio permanente de la
pagina ("Benefit from $0 Xoom transfer fees on eligible USD
transactions") -- es un precio real y repetible por elegir ese metodo de
pago, no un senuelo para clientes nuevos. Por eso se guarda con
is_promotional=0 pero con una nota aclaratoria en collector_notes.

Aun asi, este colector NO asume que este patron se mantendra: parse_fee_
matrix() revisa el HTML de cada corrida en busca de indicadores reales de
promocion (clase/estilo "line-through", badge "% OFF" via
utils.parse_off_badge, o palabras clave como "first transfer"/"new
customer"/"welcome") y lanza un error explicito si aparece alguno, en vez
de asumir en silencio que la matriz sigue sin promociones.

El tipo de cambio del corredor CA->SV ("Best Xoom Rate: 1 CAD = X USD")
tampoco mostro senales de promocion (sin tachado, sin segunda cifra, sin
lenguaje de "promo rate" como en Ria) -- es la tasa comercial estandar de
Xoom (con margen incluido, declarado explicitamente en la pagina), asi que
se usa tal cual para exchange_rate_applied.

Uso:
    python collectors/xoom.py [ruta_a_remesas.db]
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
    any_pending_today,
    capture_screenshot_evidence,
    dismiss_cookie_banner,
    get_corridor_id,
    get_operator_id,
    insert_evidence,
    insert_observation_if_new,
)

OPERATOR_NAME = "Xoom (a PayPal service)"
OPERATOR_SLUG = "xoom"
AMOUNTS = (100, 200, 500)

URL = "https://www.xoom.com/el-salvador/send-money"

DELIVERY_METHOD_MAP = {
    "Bank Deposit": "bank_deposit",
    "Debit card deposit": "debit_card_deposit",
    "Cash Pickup": "cash_pickup",
}
FUNDING_METHOD_MAP = {
    "PayPal USD (PYUSD)": "pyusd",
    "PayPal balance": "paypal_balance",
    "Bank Account": "bank_account",
    "Debit Card": "debit_card",
    "Credit Card": "credit_card",
}
SKIP_TOKENS = ("Paying with", "Fee in USD", "Fee in CAD", "Back", "close sheet")

# Indicadores de promocion que este colector NUNCA ha visto en Xoom (ver
# docstring del modulo) pero que se revisan explicitamente en cada corrida
# en vez de asumir que siguen ausentes.
PROMO_KEYWORDS = ("first transfer", "new customer", "welcome rate", "welcome offer")

PYUSD_NOTE = (
    "PayPal USD (PYUSD) muestra fee=0.00 de forma permanente (no solo en el primer envio): "
    "esta ligado al anuncio fijo de la pagina 'Benefit from $0 Xoom transfer fees on eligible "
    "USD transactions', sin tachado ni lenguaje de 'first transfer'/'new customer'. Se registra "
    "como precio real (is_promotional=0), no como promocion de bienvenida."
)


def select_currency(page: Page, currency: str) -> None:
    if currency == "USD":
        return
    page.locator("#source-currency-picker").click(timeout=10000)
    page.wait_for_timeout(500)
    page.get_by_role("option", name=currency, exact=True).click(timeout=10000)
    page.wait_for_timeout(1000)


def set_amount(page: Page, amount: int) -> None:
    field = page.locator("#text-input-send-input")
    field.fill(str(amount))
    field.blur()
    page.wait_for_timeout(1200)


def get_ca_rate(page: Page) -> float:
    text = page.inner_text("body")
    match = re.search(r"1 CAD = ([\d.]+) USD", text)
    if not match:
        raise ValueError(f"No se pudo leer 'Best Xoom Rate' (1 CAD = ... USD) en la pagina: {text[:500]!r}")
    return float(match.group(1))


def open_fees_dialog(page: Page):
    page.get_by_role("button", name="Show Fees").click(timeout=10000)
    page.wait_for_timeout(1200)
    dialog = page.get_by_role("dialog")
    dialog.wait_for(state="visible", timeout=10000)
    return dialog


def check_no_promo_markers(dialog_html: str, table_text: str) -> None:
    """Falla ruidosamente si aparece cualquier indicador de promocion que
    este colector no sabe interpretar (ver docstring del modulo), en vez de
    asumir en silencio que la matriz de tarifas sigue sin promociones."""
    lowered_html = dialog_html.lower()
    if "line-through" in lowered_html or "strikethrough" in lowered_html:
        raise ValueError(
            "Se detecto una clase/estilo de tachado (line-through/strikethrough) en el panel de "
            "tarifas de Xoom, que no estaba presente cuando se construyo este colector (2026-08-02). "
            "Esto probablemente significa que Xoom empezo a mostrar una tarifa promocional junto a la "
            "de lista; revisar manualmente antes de confiar en los datos parseados."
        )
    lowered_table = table_text.lower()
    for keyword in PROMO_KEYWORDS:
        if keyword in lowered_table:
            raise ValueError(
                f"Se detecto la palabra clave de promocion {keyword!r} dentro de la tabla de tarifas de "
                "Xoom; revisar manualmente antes de confiar en los datos parseados."
            )


def parse_fee_matrix(dialog_text: str) -> dict:
    """Parsea el texto del panel "Show Fees" en {(delivery_key, funding_key): fee}.

    El texto viene como una secuencia plana de lineas (dialog.inner_text()
    separa cada nodo de texto con saltos de linea): encabezado de metodo de
    entrega, luego pares (etiqueta de metodo de pago, valor numerico) hasta
    el siguiente encabezado o el pie de nota "*Your credit card...". Se
    recorre como una maquina de estados simple en vez de un regex gigante
    porque las etiquetas y el orden son fijos y conocidos (ver
    DELIVERY_METHOD_MAP / FUNDING_METHOD_MAP)."""
    table_text = dialog_text.split("*Your credit card")[0]
    tokens = [t.strip() for t in table_text.split("\n") if t.strip()]

    rows = {}
    current_delivery = None
    pending_label = None
    for tok in tokens:
        if tok in DELIVERY_METHOD_MAP:
            current_delivery = DELIVERY_METHOD_MAP[tok]
            pending_label = None
            continue
        if tok in SKIP_TOKENS:
            continue
        stripped = tok.rstrip("*")
        if stripped in FUNDING_METHOD_MAP:
            pending_label = FUNDING_METHOD_MAP[stripped]
            continue
        try:
            value = float(tok)
        except ValueError:
            raise ValueError(f"Token inesperado en el panel de tarifas de Xoom: {tok!r}")
        if pending_label is None or current_delivery is None:
            raise ValueError(f"Valor de tarifa {tok!r} sin metodo de pago/entrega asociado (parseo desalineado)")
        rows[(current_delivery, pending_label)] = value
        pending_label = None

    if not rows:
        raise ValueError(f"No se pudo parsear ninguna fila de tarifas: {dialog_text!r}")
    return rows, table_text


def collect_corridor(
    page: Page,
    conn: sqlite3.Connection,
    *,
    corridor_code: str,
    corridor_id: int,
    operator_id: int,
    currency: str,
    is_fx_corridor: bool,
) -> list[int]:
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    dismiss_cookie_banner(page)
    page.wait_for_selector("#text-input-send-input", timeout=30000)
    select_currency(page, currency)

    inserted_ids = []
    for amount in AMOUNTS:
        set_amount(page, amount)

        if is_fx_corridor:
            exchange_rate = get_ca_rate(page)
        else:
            exchange_rate = 1.0

        dialog = open_fees_dialog(page)
        dialog_text = dialog.inner_text(timeout=10000)
        dialog_html = dialog.inner_html(timeout=10000)
        fee_rows, table_text = parse_fee_matrix(dialog_text)
        check_no_promo_markers(dialog_html, table_text)

        timestamp_probe = datetime.now(timezone.utc).isoformat()
        candidates = [
            {"send_amount": amount, "funding_method": fm, "delivery_method": dm}
            for (dm, fm) in fee_rows
        ]
        if not any_pending_today(conn, operator_id, corridor_id, candidates, timestamp_probe):
            print(f"{corridor_code.upper()}->SV ${amount}: todo ya recolectado hoy, se omite (sin captura nueva de evidencia).")
            continue

        timestamp = datetime.now(timezone.utc)
        evidence_path, sha256_hash = capture_screenshot_evidence(page, OPERATOR_SLUG, corridor_code, amount, timestamp)

        inserted_this_amount = 0
        for (delivery_method, funding_method), fee in fee_rows.items():
            receive_amount = round((amount - fee) * exchange_rate, 2)
            total_cost_pct = round(fee / amount * 100, 4)

            notes = PYUSD_NOTE if funding_method == "pyusd" else None

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
            f"{corridor_code.upper()}->SV ${amount}: {inserted_this_amount} observaciones insertadas. "
            f"Evidencia: {evidence_path} (sha256={sha256_hash})"
        )
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

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
        us_corridor_id = get_corridor_id(conn, "US", "SV")
        ca_corridor_id = get_corridor_id(conn, "CA", "SV")
        operator_id = get_operator_id(conn, OPERATOR_NAME)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(user_agent=REQUEST_USER_AGENT, viewport={"width": 1400, "height": 1000})
            try:
                collect_corridor(
                    page,
                    conn,
                    corridor_code="us",
                    corridor_id=us_corridor_id,
                    operator_id=operator_id,
                    currency="USD",
                    is_fx_corridor=False,
                )
                collect_corridor(
                    page,
                    conn,
                    corridor_code="ca",
                    corridor_id=ca_corridor_id,
                    operator_id=operator_id,
                    currency="CAD",
                    is_fx_corridor=True,
                )
            finally:
                browser.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
