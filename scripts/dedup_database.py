#!/usr/bin/env python3
"""
Script de Limpieza y Deduplicación Unitaria para la Base de Datos job_fit.db.

Lógica:
1. Calcula y puebla la columna `fingerprint` (Empresa + Título normalizados) para todos los registros.
2. Agrupa por `fingerprint` detectando ofertas reposteadas / duplicadas con distintas URLs.
3. Para cada grupo de duplicados, conserva el PRIMER registro (el más antiguo y con mejor evaluación).
4. Elimina en cascada los registros redundantes de `cvsnapshot`, `matchresult` y `job`.
5. Ejecuta VACUUM para compactar el archivo SQLite.
"""

import logging
import sqlite3
import sys
from pathlib import Path

# Configurar path raíz del proyecto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.database.repository import get_job_fingerprint

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dedup_db")

DB_PATH = PROJECT_ROOT / "data" / "db" / "job_fit.db"


def run_database_dedup():
    if not DB_PATH.exists():
        logger.error(f"Base de datos no encontrada en: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    logger.info("=== 1. Asegurando columna e índice de huella digital (fingerprint) ===")
    try:
        cur.execute("ALTER TABLE job ADD COLUMN fingerprint VARCHAR")
    except sqlite3.OperationalError:
        pass  # Ya existe

    cur.execute("CREATE INDEX IF NOT EXISTS ix_job_fingerprint ON job (fingerprint)")
    conn.commit()

    logger.info("=== 2. Calculando huella digital para todos los registros existentes ===")
    cur.execute("SELECT id, company, title, location FROM job WHERE fingerprint IS NULL OR fingerprint = ''")
    pending_jobs = cur.fetchall()
    logger.info(f"Actualizando huella para {len(pending_jobs)} registros...")

    for jid, comp, title, loc in pending_jobs:
        fp = get_job_fingerprint(comp or "", title or "", loc or "")
        cur.execute("UPDATE job SET fingerprint = ? WHERE id = ?", (fp, jid))

    conn.commit()

    logger.info("=== 3. Analizando grupos de duplicados por huella ===")
    cur.execute("""
        SELECT fingerprint, COUNT(*) as cnt, GROUP_CONCAT(id)
        FROM job
        WHERE fingerprint IS NOT NULL AND fingerprint != ''
        GROUP BY fingerprint
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    duplicate_groups = cur.fetchall()

    if not duplicate_groups:
        logger.info("No se encontraron registros duplicados. La base de datos ya está limpia.")
        conn.close()
        return

    total_groups = len(duplicate_groups)
    total_duplicates_to_remove = sum(row[1] - 1 for row in duplicate_groups)
    logger.info(f"Se encontraron {total_groups} grupos con un total de {total_duplicates_to_remove} duplicados.")

    logger.info("=== 4. Purgando duplicados redundantes (preservando el primer aviso) ===")
    purged_jobs = 0
    purged_matches = 0
    purged_snapshots = 0

    top_companies_purged = {}

    for fp, cnt, id_str in duplicate_groups:
        ids = [int(i) for i in id_str.split(",")]

        # Obtener detalles de cada ID en el grupo para decidir cuál conservar
        placeholders = ",".join("?" for _ in ids)
        cur.execute(f"""
            SELECT j.id, j.company, j.title, j.created_at, m.score, m.tier
            FROM job j
            LEFT JOIN matchresult m ON j.id = m.job_id
            WHERE j.id IN ({placeholders})
            ORDER BY
                (m.score IS NOT NULL) DESC,  -- Priorizar los que ya tienen evaluación
                j.id ASC                     -- Si ambos tienen o no tienen evaluación, conservar el más antiguo
        """, ids)
        rows = cur.fetchall()

        keeper = rows[0]
        keeper_id = keeper[0]
        company_name = keeper[1]
        redundant_ids = [r[0] for r in rows[1:]]

        top_companies_purged[company_name] = top_companies_purged.get(company_name, 0) + len(redundant_ids)

        red_placeholders = ",".join("?" for _ in redundant_ids)

        # 1. Eliminar snapshots asociados a duplicados
        cur.execute(f"DELETE FROM cvsnapshot WHERE job_id IN ({red_placeholders})", redundant_ids)
        purged_snapshots += cur.rowcount

        # 2. Eliminar resultados de match asociados a duplicados
        cur.execute(f"DELETE FROM matchresult WHERE job_id IN ({red_placeholders})", redundant_ids)
        purged_matches += cur.rowcount

        # 3. Eliminar los jobs duplicados
        cur.execute(f"DELETE FROM job WHERE id IN ({red_placeholders})", redundant_ids)
        purged_jobs += cur.rowcount

    conn.commit()

    logger.info("=== 5. Compactando base de datos SQLite (VACUUM) ===")
    cur.execute("VACUUM")
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM job")
    remaining_jobs = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM matchresult")
    remaining_matches = cur.fetchone()[0]

    conn.close()

    print("\n" + "=" * 65)
    print("           RESUMEN DE DEDUPLICACIÓN DE BASE DE DATOS")
    print("=" * 65)
    print(f"Grupos de avisos duplicados procesados: {total_groups}")
    print(f"Vacantes duplicadas eliminadas:         {purged_jobs}")
    print(f"Evaluaciones (MatchResult) depuradas:   {purged_matches}")
    print(f"CV Snapshots depurados:                 {purged_snapshots}")
    print(f"Total de vacantes únicas vigentes en BD:{remaining_jobs}")
    print(f"Total de evaluaciones vigentes en BD:   {remaining_matches}")
    print("\nTop 10 empresas con mayor volumen de reposts purgados:")
    sorted_top = sorted(top_companies_purged.items(), key=lambda x: x[1], reverse=True)[:10]
    for comp, count in sorted_top:
        print(f"  - {comp}: {count} ofertas duplicadas eliminadas")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_database_dedup()
