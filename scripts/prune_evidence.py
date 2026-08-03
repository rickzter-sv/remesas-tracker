"""Purga archivos de evidencia del working tree, sin tocar la tabla `evidence`.

Mitad de la politica de retencion de evidencia (la otra mitad es capturar en
JPEG en vez de PNG, ver collectors/utils.py:capture_screenshot_evidence). La
fila de `evidence` (file_path, sha256_hash, captured_at) es la prueba
permanente de que algo se capturo y con que contenido exacto -- eso no se
borra nunca. Lo unico que este script borra es el archivo binario en disco,
para que el checkout de trabajo (y los commits nuevos, que ya no vuelven a
incluirlo) no sigan creciendo sin limite. El hash ya capturado sigue siendo
verificable contra cualquier copia externa (ej. un export publicado) aunque
el archivo del repo ya no exista.

Tres modos de seleccion, mutuamente excluyentes entre --days y --since:
  --days N   (default): purga archivos con evidencia REFERENCIADA mas viejos
             que N dias -- el uso normal, de higiene continua.
  --since TS: purga archivos con evidencia REFERENCIADA capturada EN o
             DESPUES de ese timestamp ISO 8601 -- para limpiar una ventana
             puntual conocida (ej. una corrida de prueba de CI), sin tocar
             nada mas viejo. --days tiene granularidad de dia completo, asi
             que no alcanza para separar "corrida legitima temprano el dia X"
             de "corrida de prueba mas tarde ese mismo dia X".
  --include-orphans: ademas, borra archivos en evidence/ que no tienen NINGUNA
             fila en `evidence` (nunca fueron prueba de nada realmente --
             tipicamente screenshots que un colector escribe a disco ANTES de
             chequear el dedup del dia, y que terminan sin fila asociada
             porque la observacion resulto duplicada). Estos se borran sin
             importar su fecha, porque no hay hash que preservar como prueba.

OJO -- lo que este script NO hace: no reescribe la historia de git. Un
archivo purgado deja de ocupar espacio en checkouts nuevos y en los commits
de ahora en adelante, pero el blob sigue viviendo en los commits viejos que
ya lo incluian (asi es git). Reducir el tamano de .git/ de verdad requeriria
reescribir historia (git filter-repo/BFG), que invalida todos los hashes de
commit posteriores y requiere force-push -- una decision aparte, mucho mas
delicada, que este script deliberadamente no toma por si solo.

Modo por defecto: dry-run (solo imprime que se borraria). Pasar --apply para
borrar de verdad.

Uso:
    python scripts/prune_evidence.py [--days 120] [--db remesas.db] [--apply]
    python scripts/prune_evidence.py --since 2026-08-02T09:00:00+00:00 --include-orphans --apply
"""

import argparse
import hashlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EVIDENCE_DIR = REPO_ROOT / "evidence"
DEFAULT_DB_PATH = REPO_ROOT / "remesas.db"
DEFAULT_RETENTION_DAYS = 120


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_prunable_referenced_files(conn: sqlite3.Connection, *, older_than: datetime = None, since: datetime = None) -> list[dict]:
    """Un archivo de evidencia puede estar referenciado por varias filas de
    `evidence` (ej. un mismo screenshot cubre 4 metodos de pago, cada uno con
    su propia fila) -- se agrupa por file_path para no intentar borrar el
    mismo archivo dos veces ni contar su tamano por duplicado."""
    rows = conn.execute(
        "SELECT file_path, sha256_hash, MIN(captured_at) AS captured_at, COUNT(*) AS n_refs "
        "FROM evidence GROUP BY file_path"
    ).fetchall()

    prunable = []
    for file_path, sha256_hash, captured_at, n_refs in rows:
        captured_dt = datetime.fromisoformat(captured_at)
        if older_than is not None and captured_dt >= older_than:
            continue
        if since is not None and captured_dt < since:
            continue
        full_path = REPO_ROOT / file_path
        if not full_path.exists():
            continue  # ya purgado en una corrida anterior
        prunable.append(
            {
                "file_path": file_path,
                "full_path": full_path,
                "sha256_hash": sha256_hash,
                "captured_at": captured_dt,
                "n_refs": n_refs,
                "size_bytes": full_path.stat().st_size,
            }
        )
    return prunable


def find_orphan_files(conn: sqlite3.Connection) -> list[dict]:
    """Archivos en evidence/ sin ninguna fila en `evidence` (ver docstring del
    modulo). No tienen sha256_hash que verificar porque nunca se guardo uno
    -- no son evidencia de nada, solo bytes sueltos."""
    referenced = {row[0] for row in conn.execute("SELECT DISTINCT file_path FROM evidence").fetchall()}
    orphans = []
    for full_path in sorted(EVIDENCE_DIR.iterdir()):
        if full_path.name == ".gitkeep" or not full_path.is_file():
            continue
        rel_path = f"evidence/{full_path.name}"
        if rel_path in referenced:
            continue
        orphans.append({"file_path": rel_path, "full_path": full_path, "size_bytes": full_path.stat().st_size})
    return orphans


def git_remove(rel_path: str) -> None:
    subprocess.run(
        ["git", "rm", "-q", "--ignore-unmatch", "--", rel_path],
        cwd=REPO_ROOT,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Ruta a remesas.db")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--days", type=int, default=None,
        help=f"Purgar evidencia referenciada de mas de N dias (default {DEFAULT_RETENTION_DAYS} si no se da --since)",
    )
    selection.add_argument(
        "--since", type=str, default=None,
        help="Purgar evidencia referenciada capturada EN o DESPUES de este timestamp ISO 8601 (ej. 2026-08-02T09:00:00+00:00)",
    )
    parser.add_argument(
        "--include-orphans", action="store_true",
        help="Tambien borrar archivos en evidence/ sin ninguna fila en la tabla evidence (cualquier fecha)",
    )
    parser.add_argument("--apply", action="store_true", help="Borrar de verdad (sin esto, solo simula)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"Error: no existe la base de datos en {db_path}")

    older_than = None
    since = None
    if args.since is not None:
        since = datetime.fromisoformat(args.since)
        criterio = f"capturados desde {since.isoformat()}"
    else:
        days = args.days if args.days is not None else DEFAULT_RETENTION_DAYS
        older_than = datetime.now(timezone.utc) - timedelta(days=days)
        criterio = f"de mas de {days} dias"

    conn = sqlite3.connect(db_path)
    try:
        prunable = find_prunable_referenced_files(conn, older_than=older_than, since=since)
        orphans = find_orphan_files(conn) if args.include_orphans else []
    finally:
        conn.close()

    all_items = [(item, False) for item in prunable] + [(item, True) for item in orphans]

    if not all_items:
        extra = " ni huerfanos" if args.include_orphans else ""
        print(f"Nada que purgar: no hay evidencia {criterio} que siga en disco{extra}.")
        return

    total_bytes = 0
    skipped_mismatch = []
    removed = []

    for item, is_orphan in sorted(all_items, key=lambda pair: pair[0].get("captured_at") or datetime.min.replace(tzinfo=timezone.utc)):
        tag = "HUERFANO, sin fila evidence" if is_orphan else f"{item['n_refs']} fila(s) de evidence"
        label = f"{item['file_path']} ({item['size_bytes'] / 1024:.0f} KB, {tag})"

        if not args.apply:
            print(f"[dry-run] se borraria: {label}")
            total_bytes += item["size_bytes"]
            continue

        if not is_orphan:
            actual_hash = sha256_of(item["full_path"])
            if actual_hash != item["sha256_hash"]:
                skipped_mismatch.append(item["file_path"])
                print(f"OMITIDO (hash en disco no coincide con evidence.sha256_hash, no se toca): {label}")
                continue

        git_remove(item["file_path"])
        total_bytes += item["size_bytes"]
        removed.append(item["file_path"])
        print(f"Purgado: {label}")

    print()
    print(f"Total: {len(all_items)} archivo(s), {total_bytes / (1024 * 1024):.1f} MB "
          f"{'que se borrarian (dry-run)' if not args.apply else 'liberados'}.")
    if skipped_mismatch:
        print(f"{len(skipped_mismatch)} archivo(s) omitidos por hash no coincidente -- revisar manualmente.")
    if args.apply and removed:
        print(
            f"\n{len(removed)} archivo(s) marcados para borrado con 'git rm' (ya en el staging area).\n"
            "Las filas de evidence (para los no huerfanos) NO se tocaron: siguen probando que la\n"
            "captura existio, con su hash. Falta comitear el borrado a mano, por ejemplo:\n"
            f'  git commit -m "purga de evidencia {criterio}"'
        )
    elif not args.apply:
        print("\nEsto fue un dry-run -- no se borro ni modifico nada. Volver a correr con --apply para aplicar.")


if __name__ == "__main__":
    main()
