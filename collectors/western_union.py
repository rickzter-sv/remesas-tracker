"""Colector de tarifas de Western Union (US->SV y CA->SV) via Playwright.

A diferencia de Remitly, Western Union no expone una tabla estatica de
tarifas: el desglose por metodo de pago solo aparece despues de interactuar
con la SPA (monto -> metodo de entrega -> metodo de pago), asi que este
colector usa Playwright en vez de requests/BeautifulSoup.

Regla anti-promocion: en ambos sitios (US y CA), casi todos los metodos de
pago muestran actualmente un descuento de "primer envio" que lleva la
comision mostrada por defecto a $0.00 (o a un valor parcial, ej. Credit
Card en US: 40% OFF). La comision real/recurrente (de lista) SI aparece en
pantalla, pero hay que leerla explicitamente:
  - En US: en el panel "Summary" (section.trxn-summary), como un precio
    tachado junto a un badge "NN% OFF $X.XX" (span.strikethrough).
  - En CA: en el panel deslizante "Payment method" (abierto con el boton
    "Change"), que muestra AMBOS valores por fila: "+ X.XX CAD" (lista) y
    "Y.YY CAD" (ya con el descuento aplicado).
Este colector siempre guarda la tarifa de LISTA en `fee`, marca
is_promotional=1 cuando lista != lo efectivamente cobrado, y deja el monto
promocional documentado en collector_notes (nunca en `fee`).

Uso:
    python collectors/western_union.py [ruta_a_remesas.db]
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
    click_with_modal_retry,
    dismiss_any_blocking_modal,
    dismiss_cookie_banner,
    get_corridor_id,
    get_operator_id,
    insert_evidence,
    insert_observation_if_new,
    is_promotional,
    parse_off_badge,
)

OPERATOR_NAME = "Western Union"
OPERATOR_SLUG = "western_union"
AMOUNTS = (100, 200, 500)

US_URL = "https://www.westernunion.com/us/en/web/send-money/start?ReceiveCountry=SV"
CA_URL = "https://www.westernunion.com/ca/en/web/send-money/start?ReceiveCountry=SV"

# El sitio US expone estos ids de forma estable en el radiogroup de "How
# would you like to pay?" (confirmado inspeccionando el DOM manualmente).
US_PAYMENT_METHODS = [
    ("fundsIn_CreditCard", "credit_card"),
    ("fundsIn_ACH", "bank_account"),
    ("fundsIn_GPAY", "google_pay"),
    ("fundsIn_DebitCard", "debit_card"),
    ("fundsIn_apple_pay", "apple_pay"),
]
US_DELIVERY_METHOD_ID = "fundsOut_BA"  # "Bank account" -- delivery method fijo para esta corrida

# El sitio CA no expone ids estables; el panel "Payment method" lista estas
# etiquetas de texto (confirmado manualmente). No incluye Apple Pay, pero si
# "Interac e-Transfer" (metodo propio de Canada).
CA_PAYMENT_METHOD_LABELS = [
    ("Debit card", "debit_card"),
    ("Bank account via Plaid", "bank_account"),
    ("Interac e-Transfer", "interac_e_transfer"),
    ("Credit card", "credit_card"),
    ("Google Pay", "google_pay"),
]
CA_DELIVERY_METHOD_LABEL = "Bank account"  # delivery method fijo para esta corrida


def dismiss_wallet_modal(page: Page) -> None:
    """Google Pay / Apple Pay abren un modal explicando que el metodo debe
    estar configurado en el dispositivo (no aplica en un navegador
    automatizado sin wallet real). El modal no bloquea la lectura de datos
    del DOM, pero si bloquea clics posteriores, asi que hay que cerrarlo
    antes de seleccionar el siguiente metodo de pago."""
    for modal_id in ("google-pay", "apple-pay"):
        modal = page.locator(f"#{modal_id}")
        try:
            if modal.count() and modal.is_visible():
                ok_btn = modal.get_by_role("button", name="OK")
                if ok_btn.count():
                    ok_btn.first.click(timeout=3000)
                else:
                    page.keyboard.press("Escape")
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass


def parse_us_summary(raw_text: str) -> tuple[float, float, bool]:
    """Parsea section.trxn-summary. Devuelve (fee_lista, fee_cobrado, is_promo).

    El texto trae espacios NBSP (\\xa0, normalizados abajo) y ademas una
    etiqueta de accesibilidad "- Discounted Price" pegada justo despues del
    precio tachado y del precio final (ej. "100% OFF 2.99 - Discounted Price
    USD"), asi que el regex de la tarifa de lista NO debe exigir que "USD"
    venga inmediatamente despues del numero.
    """
    text = re.sub(r"\s+", " ", raw_text)
    off_badge = parse_off_badge(text)
    fee_actual_match = re.search(r"Transfer fee.*?\+\s*([\d.]+)\s*USD", text)
    if not fee_actual_match:
        raise ValueError(f"No se pudo leer 'Transfer fee' en el resumen: {text!r}")
    fee_actual = float(fee_actual_match.group(1))
    if off_badge:
        _, fee_list = off_badge
        is_promo = is_promotional(fee_list, fee_actual)
    else:
        fee_list = fee_actual
        is_promo = False
    return fee_list, fee_actual, is_promo


def set_amount_us(page: Page, amount: int) -> None:
    dismiss_any_blocking_modal(page)
    field = page.get_by_placeholder("Send amount")
    click_with_modal_retry(page, field)
    field.fill("")
    field.fill(str(amount))
    field.blur()
    page.wait_for_timeout(1000)


def collect_us(page: Page, conn: sqlite3.Connection, corridor_id: int, operator_id: int) -> list:
    page.goto(US_URL, wait_until="domcontentloaded", timeout=60000)
    dismiss_cookie_banner(page)
    page.wait_for_selector('input[placeholder="Send amount"]', timeout=30000)

    inserted_ids = []
    for amount in AMOUNTS:
        set_amount_us(page, amount)

        delivery_radio = page.locator(f"#{US_DELIVERY_METHOD_ID}")
        delivery_radio.wait_for(state="visible", timeout=15000)
        click_with_modal_retry(page, delivery_radio)
        page.wait_for_selector('[role="radiogroup"] [role="radio"]', timeout=15000)
        page.wait_for_timeout(800)

        timestamp = datetime.now(timezone.utc)
        evidence_path, sha256_hash = capture_screenshot_evidence(page, OPERATOR_SLUG, "us", amount, timestamp)

        inserted_this_amount = 0
        for payment_id, funding_method in US_PAYMENT_METHODS:
            radio = page.locator(f"#{payment_id}")
            radio.wait_for(state="visible", timeout=15000)
            click_with_modal_retry(page, radio)
            page.wait_for_timeout(700)
            dismiss_wallet_modal(page)
            dismiss_any_blocking_modal(page)

            summary_text = page.locator("section.trxn-summary").inner_text(timeout=10000)
            fee_list, fee_actual, is_promo = parse_us_summary(summary_text)

            receive_amount = round((amount - fee_list) * 1.0, 2)
            total_cost_pct = round(fee_list / amount * 100, 4)

            notes = None
            if is_promo:
                notes = (
                    f"Tarifa de lista (real/recurrente) = {fee_list:.2f} USD, leida del precio tachado "
                    f"en section.trxn-summary de la pagina de Western Union. El sitio muestra actualmente "
                    f"un descuento promocional de primer envio que cobra {fee_actual:.2f} USD en su lugar; "
                    f"ese valor promocional NO se usa como fee ni para receive_amount/total_cost_pct."
                )

            observation = {
                "timestamp_utc": timestamp.isoformat(),
                "corridor_id": corridor_id,
                "operator_id": operator_id,
                "send_amount": amount,
                "funding_method": funding_method,
                "delivery_method": "bank_account",
                "fee": fee_list,
                "exchange_rate_applied": 1.0,
                "receive_amount": receive_amount,
                "total_cost_pct": total_cost_pct,
                "is_promotional": int(is_promo),
                "collection_method": "automated",
                "source_url": US_URL,
                "collector_notes": notes,
            }
            observation_id = insert_observation_if_new(conn, observation)
            if observation_id is None:
                continue
            insert_evidence(conn, observation_id, evidence_path, sha256_hash, timestamp.isoformat())
            inserted_ids.append(observation_id)
            inserted_this_amount += 1

        conn.commit()
        print(f"US->SV ${amount}: {inserted_this_amount} observaciones insertadas. Evidencia: {evidence_path} (sha256={sha256_hash})")

    return inserted_ids


def select_ca_country(page: Page) -> None:
    search_box = page.get_by_placeholder("Country/region or currency")
    search_box.click()
    search_box.fill("El Salvador")
    result = page.get_by_text("El Salvador", exact=True).last
    result.wait_for(state="visible", timeout=15000)
    result.click()
    page.wait_for_selector("text=You send", timeout=20000)


def set_amount_ca(page: Page, amount: int) -> None:
    amount_field = page.locator('input[type="text"]').first
    amount_field.click()
    amount_field.fill("")
    amount_field.fill(str(amount))
    amount_field.blur()
    page.wait_for_timeout(1000)


def open_ca_panel(page: Page, which: str) -> str:
    """which: 'payment' (nth 0) o 'delivery' (nth 1). Devuelve el texto del panel."""
    index = 0 if which == "payment" else 1
    change_buttons = page.locator("button", has_text="Change")
    change_buttons.nth(index).click()
    dialog = page.locator('[role="dialog"]').last
    dialog.wait_for(state="visible", timeout=15000)
    page.wait_for_timeout(500)
    text = dialog.inner_text(timeout=10000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    return text


def parse_ca_delivery_rate(panel_text: str, delivery_label: str) -> float:
    text = re.sub(r"\s+", " ", panel_text)
    match = re.search(re.escape(delivery_label) + r".*?1 CAD = ([\d.]+) USD", text)
    if not match:
        raise ValueError(f"No se encontro tipo de cambio para {delivery_label!r} en: {text!r}")
    return float(match.group(1))


def parse_ca_payment_methods(panel_text: str) -> dict:
    text = re.sub(r"\s+", " ", panel_text)
    results = {}
    for label, key in CA_PAYMENT_METHOD_LABELS:
        match = re.search(re.escape(label) + r".*?\+\s*([\d.]+)\s*CAD\s*([\d.]+)\s*CAD", text)
        if not match:
            continue
        fee_list = float(match.group(1))
        fee_actual = float(match.group(2))
        results[key] = (fee_list, fee_actual, is_promotional(fee_list, fee_actual))
    return results


def collect_ca(page: Page, conn: sqlite3.Connection, corridor_id: int, operator_id: int) -> list:
    page.goto(CA_URL, wait_until="domcontentloaded", timeout=60000)
    dismiss_cookie_banner(page)
    select_ca_country(page)

    inserted_ids = []
    for amount in AMOUNTS:
        set_amount_ca(page, amount)

        delivery_panel_text = open_ca_panel(page, "delivery")
        exchange_rate = parse_ca_delivery_rate(delivery_panel_text, CA_DELIVERY_METHOD_LABEL)

        payment_panel_text = open_ca_panel(page, "payment")

        timestamp = datetime.now(timezone.utc)
        evidence_path, sha256_hash = capture_screenshot_evidence(page, OPERATOR_SLUG, "ca", amount, timestamp)

        fees_by_method = parse_ca_payment_methods(payment_panel_text)
        if not fees_by_method:
            raise ValueError(f"No se pudo parsear ningun metodo de pago del panel CA: {payment_panel_text!r}")

        inserted_this_amount = 0
        for label, funding_method in CA_PAYMENT_METHOD_LABELS:
            if funding_method not in fees_by_method:
                continue
            fee_list, fee_actual, is_promo = fees_by_method[funding_method]

            receive_amount = round((amount - fee_list) * exchange_rate, 2)
            total_cost_pct = round(fee_list / amount * 100, 4)

            notes = None
            if is_promo:
                notes = (
                    f"Tarifa de lista (real/recurrente) = {fee_list:.2f} CAD, leida del panel deslizante "
                    f"'Payment method' de Western Union (columna izquierda, antes del descuento). El sitio "
                    f"muestra actualmente un descuento promocional que cobra {fee_actual:.2f} CAD en su "
                    f"lugar; ese valor promocional NO se usa como fee ni para receive_amount/total_cost_pct."
                )

            observation = {
                "timestamp_utc": timestamp.isoformat(),
                "corridor_id": corridor_id,
                "operator_id": operator_id,
                "send_amount": amount,
                "funding_method": funding_method,
                "delivery_method": "bank_account",
                "fee": fee_list,
                "exchange_rate_applied": exchange_rate,
                "receive_amount": receive_amount,
                "total_cost_pct": total_cost_pct,
                "is_promotional": int(is_promo),
                "collection_method": "automated",
                "source_url": CA_URL,
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
        us_corridor_id = get_corridor_id(conn, "US", "SV")
        ca_corridor_id = get_corridor_id(conn, "CA", "SV")
        operator_id = get_operator_id(conn, OPERATOR_NAME)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(user_agent=REQUEST_USER_AGENT, viewport={"width": 1400, "height": 1000})
            try:
                collect_us(page, conn, us_corridor_id, operator_id)
                collect_ca(page, conn, ca_corridor_id, operator_id)
            finally:
                browser.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
