"""Utilidades compartidas entre colectores basados en Playwright.

Agrupa lo que western_union.py necesitaba resolver de forma generica para
cualquier SPA de cotizador: cerrar modales bloqueantes ad-hoc, reintentar
clics cuando un modal intercepta el puntero, y detectar el patron de
descuento promocional "NN% OFF $X.XX" que varios operadores (no solo WU)
usan para mostrar la tarifa de lista tachada junto al precio con descuento.

Tambien centraliza el acceso a la base de datos (lookups de corredor/
operador, insercion de observations/evidence) para que cada colector no
reimplemente el mismo SQL.
"""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

REPO_ROOT = Path(__file__).parent.parent
EVIDENCE_DIR = REPO_ROOT / "evidence"
DEFAULT_DB_PATH = REPO_ROOT / "remesas.db"

REQUEST_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

COOKIE_BANNER_LABELS = ("Accept All Cookies", "Accept all", "I Accept", "Accept")
MODAL_CLOSE_LABELS = ("OK", "Got it", "Close", "Continue")


def get_corridor_id(conn: sqlite3.Connection, origin: str, destination: str) -> int:
    row = conn.execute(
        "SELECT corridor_id FROM corridors WHERE origin_country = ? AND destination_country = ?",
        (origin, destination),
    ).fetchone()
    if row is None:
        raise LookupError(f"Corredor {origin}->{destination} no existe en la tabla corridors")
    return row[0]


def get_operator_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT operator_id FROM operators WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise LookupError(f"Operador {name!r} no existe en la tabla operators")
    return row[0]


def insert_observation(conn: sqlite3.Connection, observation: dict) -> int:
    columns = ", ".join(observation.keys())
    placeholders = ", ".join("?" for _ in observation)
    cursor = conn.execute(
        f"INSERT INTO observations ({columns}) VALUES ({placeholders})",
        tuple(observation.values()),
    )
    return cursor.lastrowid


def already_collected_today(conn: sqlite3.Connection, observation: dict) -> bool:
    """True si ya existe una fila con la misma combinacion operator_id/
    corridor_id/send_amount/funding_method/delivery_method para la FECHA
    (no el timestamp exacto) de observation['timestamp_utc'].

    Se usa la fecha del propio timestamp que el colector ya calculo (en vez
    de la hora del reloj de SQLite en el momento del INSERT) para que el
    criterio sea deterministico y facil de probar, y para que una corrida
    que cruza la medianoche UTC quede consistente consigo misma.

    funding_method/delivery_method pueden ser NULL (ej. Remitly en US solo
    tiene delivery_method, sin funding_method): se compara con `IS` en vez
    de `=` para que NULL matchee NULL correctamente en vez de no matchear
    nunca, que es como se comporta `=` con NULL en SQL.
    """
    date_str = observation["timestamp_utc"][:10]  # "YYYY-MM-DD"
    row = conn.execute(
        """
        SELECT 1 FROM observations
        WHERE operator_id = ? AND corridor_id = ? AND send_amount = ?
          AND funding_method IS ? AND delivery_method IS ?
          AND date(timestamp_utc) = ?
        LIMIT 1
        """,
        (
            observation["operator_id"],
            observation["corridor_id"],
            observation["send_amount"],
            observation.get("funding_method"),
            observation.get("delivery_method"),
            date_str,
        ),
    ).fetchone()
    return row is not None


def describe_observation(conn: sqlite3.Connection, observation: dict) -> str:
    """Descripcion legible de una observacion para mensajes de log (dedup,
    errores), resolviendo operator_id/corridor_id a nombres/codigos."""
    operator_row = conn.execute(
        "SELECT name FROM operators WHERE operator_id = ?", (observation["operator_id"],)
    ).fetchone()
    corridor_row = conn.execute(
        "SELECT origin_country, destination_country FROM corridors WHERE corridor_id = ?",
        (observation["corridor_id"],),
    ).fetchone()
    operator_label = operator_row[0] if operator_row else f"operator_id={observation['operator_id']}"
    corridor_label = f"{corridor_row[0]}->{corridor_row[1]}" if corridor_row else f"corridor_id={observation['corridor_id']}"
    return (
        f"{operator_label} {corridor_label} ${observation['send_amount']:g} "
        f"(funding={observation.get('funding_method')}, delivery={observation.get('delivery_method')})"
    )


def insert_observation_if_new(conn: sqlite3.Connection, observation: dict) -> Optional[int]:
    """Como insert_observation(), pero no inserta (ni duplica ni reemplaza)
    si ya existe una fila equivalente recolectada el mismo dia (ver
    already_collected_today). Devuelve el observation_id insertado, o None
    si se omitio por ya estar recolectada hoy -- quien llama debe chequear
    None y saltarse tambien el insert_evidence correspondiente.

    Pensado para que la corrida diaria automatizada (run_all.py via
    GitHub Actions) sea segura de re-ejecutar el mismo dia sin duplicar
    observaciones."""
    if already_collected_today(conn, observation):
        print(f"  YA RECOLECTADO HOY, se omite: {describe_observation(conn, observation)}")
        return None
    return insert_observation(conn, observation)


def insert_evidence(
    conn: sqlite3.Connection, observation_id: int, file_path: str, sha256_hash: str, captured_at: str
) -> None:
    conn.execute(
        "INSERT INTO evidence (observation_id, file_path, sha256_hash, captured_at) VALUES (?, ?, ?, ?)",
        (observation_id, file_path, sha256_hash, captured_at),
    )


SCREENSHOT_JPEG_QUALITY = 70


def capture_screenshot_evidence(
    page: Page,
    operator_slug: str,
    corridor_code: str,
    amount: int,
    timestamp: datetime,
    full_page: bool = True,
) -> tuple[str, str]:
    """Toma la captura y la guarda como evidencia, en un solo paso.

    JPEG (no PNG) a proposito: estas paginas son cotizadores con fotos/
    gradientes, donde JPEG calidad 70 es visualmente indistinguible para el
    proposito de verificar un monto/tarifa en pantalla, pero pesa ~10-20% de
    lo que pesa el PNG sin perdida equivalente (confirmado: una captura de
    Xoom paso de 3.4MB a ~350-450KB). Con 5 colectores corriendo a diario
    dentro de un repo Git que nunca borra su historia, ese ahorro es lo que
    separa un repo sostenible de uno que llega al limite blando de 5GB de
    GitHub en semanas en vez de meses (ver scripts/prune_evidence.py para la
    otra mitad de la politica de retencion: purga de archivos viejos)."""
    jpeg_bytes = page.screenshot(full_page=full_page, type="jpeg", quality=SCREENSHOT_JPEG_QUALITY)
    filename = f"{operator_slug}_{corridor_code}_{amount}_{timestamp.strftime('%Y%m%dT%H%M%SZ')}.jpg"
    path = EVIDENCE_DIR / filename
    path.write_bytes(jpeg_bytes)
    sha256_hash = hashlib.sha256(jpeg_bytes).hexdigest()
    return f"evidence/{filename}", sha256_hash


def dismiss_cookie_banner(page: Page, labels: tuple = COOKIE_BANNER_LABELS) -> None:
    for text in labels:
        try:
            btn = page.get_by_role("button", name=text, exact=False)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=2000)
                return
        except Exception:
            continue


def dismiss_any_blocking_modal(
    page: Page, modal_selector: str = ".modal.in", labels: tuple = MODAL_CLOSE_LABELS
) -> None:
    """Cierra modales "educativos" ad-hoc que interceptan clics posteriores.

    Generico a proposito: en vez de listar cada id de modal posible (hay
    varios y cambian entre operadores), busca cualquier elemento visible que
    matchee `modal_selector` y prueba una lista corta de labels de boton de
    cierre comunes; si ninguno aplica, cae a Escape. `modal_selector` es
    configurable porque cada sitio usa su propia convencion de clase para
    marcar un modal como abierto (WU usa Bootstrap ".modal.in").
    """
    try:
        modals = page.locator(modal_selector)
        for i in range(modals.count()):
            modal = modals.nth(i)
            if not modal.is_visible():
                continue
            closed = False
            for label in labels:
                btn = modal.get_by_role("button", name=label)
                if btn.count():
                    try:
                        btn.first.click(timeout=2000)
                        page.wait_for_timeout(300)
                        closed = True
                        break
                    except Exception:
                        continue
            if not closed:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
    except Exception:
        pass


def click_with_modal_retry(page: Page, locator, dismiss_fn=dismiss_any_blocking_modal, attempts: int = 3) -> None:
    last_error = None
    for _ in range(attempts):
        try:
            locator.click(timeout=10000)
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
            dismiss_fn(page)
    raise last_error


def parse_off_badge(text: str, pattern: str = r"(\d+)%\s*OFF\s*([\d.]+)"):
    """Busca un badge de descuento tipo "NN% OFF $X.XX" en texto ya normalizado.

    No asume que $0.00 sea la unica forma de promocion: varios operadores
    (WU confirmado, posiblemente otros) muestran descuentos PARCIALES bajo
    el mismo patron de badge (ej. "40% OFF 2.99"), no solo el 100% OFF que
    lleva la tarifa mostrada a cero. Devuelve (pct, fee_lista) o None si no
    se encontro el badge; quien llama decide como comparar fee_lista contra
    la tarifa efectivamente cobrada.
    """
    import re

    match = re.search(pattern, text)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def is_promotional(fee_list: float, fee_actual: float, tolerance: float = 0.001) -> bool:
    return abs(fee_list - fee_actual) > tolerance
