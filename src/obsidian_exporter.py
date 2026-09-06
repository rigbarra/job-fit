import os
import re
import logging
from datetime import UTC, datetime, date
from pathlib import Path
from sqlmodel import Session, select

from config.settings import load_config, settings
from src.database.repository import engine, get_obsidian_last_sync, set_obsidian_last_sync
from src.database.models import Job, MatchResult, CVSnapshot
from src.agent.filter import extract_modality_and_country, parse_salary_details
from src.main import is_local_location

logger = logging.getLogger("obsidian-exporter")


def to_wsl_unc_path(linux_path: Path, distro: str = "Debian") -> str:
    r"""Convierte una ruta Linux/WSL a UNC de Windows (\\wsl$\Debian\...) para abrir desde Obsidian/Windows."""
    resolved = str(linux_path.resolve()).replace("/", "\\")
    return f"\\\\wsl$\\{distro}{resolved}"


def to_file_url(path: Path) -> str:
    """Convierte una ruta /mnt/c/... en file:///C:/... para Obsidian Windows. Ruta Linux → as_uri()."""
    p_str = str(path.resolve()).replace("\\", "/")
    if p_str.startswith("/mnt/") and len(p_str) > 6 and p_str[6] == "/":
        drive = p_str[5].upper()
        rest = p_str[6:]
        return f"file:///{drive}:{rest}"
    return path.resolve().as_uri()


def to_display_path(path: Path) -> str:
    r"""Convierte /mnt/c/... → C:\... para mostrar, o mantiene la ruta Linux nativa."""
    p_str = str(path.resolve())
    if p_str.startswith("/mnt/") and len(p_str) > 6 and p_str[6] == "/":
        drive = p_str[5].upper()
        rest = p_str[6:].replace("/", "\\")
        return f"{drive}:{rest}"
    return p_str


# Retrocompatibilidad
to_windows_file_url = to_file_url
to_windows_display_path = to_display_path


def sanitize_filename(text: str) -> str:
    """Limpia caracteres especiales para nombres de archivo válidos en cualquier S.O."""
    clean = re.sub(r'[^\w\s-]', '', text or '').strip()
    clean = re.sub(r'[\s-]+', '_', clean)
    return clean[:50]


def is_job_notified(job: Job, match: MatchResult, notification_rules: dict, search_filters: dict) -> bool:
    """Verifica si la vacante cumple las reglas para ser notificada a Discord."""
    min_score = float(notification_rules.get("min_score_to_notify", 75.0))
    if match.score < min_score:
        return False

    is_local = is_local_location(job.location)
    group_key = "national" if is_local else "international"
    tier_key = f"allow_tier_{match.tier}"
    if not bool(notification_rules.get(group_key, {}).get(tier_key, False)):
        return False

    excluded_comps = search_filters.get("excluded_companies", [])
    if any(ex.lower() in (job.company or "").lower() for ex in excluded_comps):
        return False

    return True


def _resolve_vault(base_dir: Path | None, obsidian_cfg: dict) -> Path:
    """Determina el directorio raíz del vault de Obsidian de forma agnóstica al S.O."""
    if base_dir:
        return base_dir
    configured_vault = obsidian_cfg.get("vault_path")
    if configured_vault:
        cfg_p = Path(configured_vault)
        if str(cfg_p).startswith("/mnt/") and not Path("/mnt/c").exists():
            return Path(settings.project_root) / "output" / "obsidian"
        return cfg_p
    if Path("/mnt/c").exists():
        return Path("/mnt/c/job-fit-obsidian")
    return Path(settings.project_root) / "output" / "obsidian"


def _purge_orphaned_md(jobs_dir: Path, active_card_filenames: set[str]) -> set[int]:
    """Elimina fichas .md huérfanas (tarjeta borrada en el Kanban) y su PDF generado.
    Retorna los job_ids eliminados para excluirlos del render."""
    deleted_job_ids: set[int] = set()
    if not jobs_dir.exists():
        return deleted_job_ids
    for md_file in jobs_dir.glob("*.md"):
        if md_file.name in active_card_filenames:
            continue
        try:
            m_text = md_file.read_text(encoding="utf-8")
            jid_match = re.search(r'^job_id:\s*(\d+)', m_text, re.MULTILINE)
            if jid_match:
                deleted_job_ids.add(int(jid_match.group(1)))
            # Borrar el PDF generado (ruta guardada en el frontmatter pdf_path)
            pdf_match = re.search(r'^pdf_path:\s*"([^"]+)"', m_text, re.MULTILINE)
            if pdf_match:
                pdf_file = Path(pdf_match.group(1))
                if pdf_file.exists():
                    pdf_file.unlink()
                    logger.info(f"Purgado CV PDF huérfano: {pdf_file.name}")
            md_file.unlink()
            logger.info(f"Purgada ficha huérfana: {md_file.name}")
        except Exception as ex:
            logger.warning(f"Error purgando {md_file.name}: {ex}")
    return deleted_job_ids


def sync_obsidian_vault(base_dir: Path | None = None) -> dict:
    """
    Sincronización incremental con Obsidian.
    Solo exporta vacantes nuevas desde la última sync exitosa (marca de agua en SQLite).
    Purga .md huérfanos cuando el usuario elimina tarjetas del Kanban.
    Sin carpeta CVs/, sin visor embebido. Link UNC directo al PDF original en WSL.
    """
    config = load_config()
    obsidian_cfg = config.get("obsidian", {})\
    
    search_filters = config.get("search_filters", {})
    notification_rules = config.get("notification_rules", {})

    target_dir = _resolve_vault(base_dir, obsidian_cfg)
    jobs_dir = target_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    # CVs/ ya no se crea — los links apuntan al PDF original en Linux/WSL

    kanban_path = target_dir / "Tablero_Postulaciones.md"
    auto_clean_orphans = bool(obsidian_cfg.get("auto_clean_orphans", True))
    summary = {"total_jobs": 0, "cards_created": 0, "kanban_file": str(kanban_path)}

    # ─── 1. Leer tarjetas activas en el Kanban y su columna actual ───────────
    # La fuente de verdad del estado (etapa) ES el Kanban, no el frontmatter del .md
    # card_current_col[filename] = columna actual según el Kanban
    card_current_col: dict[str, str] = {}
    if kanban_path.exists():
        try:
            k_text = kanban_path.read_text(encoding="utf-8")
            current_col = ""
            for line in k_text.splitlines():
                # Detectar encabezado de columna
                if line.startswith("## "):
                    current_col = line[3:].strip()
                # Detectar referencia a tarjeta en la columna actual
                m = re.search(r'\[\[jobs/([^\|\]]+\.md)', line)
                if m and current_col:
                    card_current_col[m.group(1)] = current_col
        except Exception as ex:
            logger.warning(f"Error leyendo Kanban: {ex}")

    active_card_filenames = set(card_current_col.keys())

    # ─── 2. Purgar fichas huérfanas (tarjeta borrada en Obsidian) ─────────────
    deleted_job_ids: set[int] = set()
    if auto_clean_orphans and active_card_filenames:
        deleted_job_ids = _purge_orphaned_md(jobs_dir, active_card_filenames)

    # ─── 3. Determinar ventana temporal incremental ───────────────────────────
    last_sync = get_obsidian_last_sync()
    # Si nunca se ha sincronizado, usar el start_cutoff de config como punto de inicio
    if last_sync is None:
        cutoff_str = obsidian_cfg.get("start_cutoff")
        if cutoff_str:
            try:
                last_sync = datetime.strptime(cutoff_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                last_sync = datetime(2026, 9, 4, 21, 10, 0)
        else:
            last_sync = datetime(2026, 9, 4, 21, 10, 0)
    # Asegurar que sea naive (sin tzinfo) para comparar con la BD
    if last_sync.tzinfo is not None:
        last_sync = last_sync.replace(tzinfo=None)

    sync_start = datetime.now(tz=UTC).replace(tzinfo=None)

    # ─── 4. Consultar solo vacantes nuevas desde last_sync ────────────────────
    with Session(engine) as session:
        applied_since = select(CVSnapshot.job_id).where(CVSnapshot.created_at >= last_sync)
        results = session.exec(
            select(Job, MatchResult)
            .outerjoin(MatchResult, Job.id == MatchResult.job_id)
            .where(
                (Job.id.in_(applied_since)) |
                ((MatchResult.score >= 75.0) & (MatchResult.created_at >= last_sync))
            )
        ).all()

        summary["total_jobs"] = len(results)

        kanban_columns = {
            "📥 Bandeja Notificados (Discord)": [],
            "📤 Aplicado": [],
            "📞 Primer Contacto (Recruiter)": [],
            "🧠 Prueba Psicológica": [],
            "💻 Prueba Técnica": [],
            "👔 Reunión Jefatura / Manager": [],
            "📜 Carta Oferta": [],
            "👻 Ghosted / Sin Respuesta": [],
            "❌ Rechazado": [],
        }

        today_date = date.today()

        for job, match in results:
            if job.id in deleted_job_ids:
                continue

            if not match:
                class DummyMatch:
                    score = 80.0
                    tier = 1
                    rationale = "Postulación previa importada."
                    missing_keywords = "[]"
                    created_at = today_date
                match = DummyMatch()

            snapshot = session.exec(
                select(CVSnapshot).where(CVSnapshot.job_id == job.id).order_by(CVSnapshot.id.desc())
            ).first()

            pub_date = match.created_at.date() if (match and match.created_at) else today_date
            pub_date_str = pub_date.strftime("%Y-%m-%d")

            clean_company = sanitize_filename(job.company)
            clean_title = sanitize_filename(job.title)
            file_name = f"{pub_date_str}_{clean_company}_{clean_title}.md"
            card_path = jobs_dir / file_name

            # ─── Preservar estado desde el Kanban (fuente de verdad) ─────────
            # El usuario mueve tarjetas en Obsidian → el Kanban cambia, el .md NO.
            # Por eso leemos la columna actual del Kanban, no del frontmatter del .md.
            existing_etapa = card_current_col.get(file_name)  # None si es tarjeta nueva
            existing_exp_sal = ""
            existing_contacto = ""
            existing_notes_body = None

            if card_path.exists():
                try:
                    old_text = card_path.read_text(encoding="utf-8")
                    m_exp = re.search(r'^expectativa_salarial:\s*"(.*?)"', old_text, re.MULTILINE)
                    if m_exp:
                        existing_exp_sal = m_exp.group(1)
                    m_contact = re.search(r'^contacto_reclutador:\s*"(.*?)"', old_text, re.MULTILINE)
                    if m_contact:
                        existing_contacto = m_contact.group(1)
                    if "### 🗣️ Bitácora de Entrevistas & Notas" in old_text:
                        parts = old_text.split("### 🗣️ Bitácora de Entrevistas & Notas")
                        body_after = parts[1]
                        if "### 📄 Descripción Original de la Oferta" in body_after:
                            body_after = body_after.split("### 📄 Descripción Original de la Oferta")[0]
                        existing_notes_body = body_after.strip()
                except Exception as ex:
                    logger.warning(f"Error leyendo ficha existente {file_name}: {ex}")

            should_notify = is_job_notified(job, match, notification_rules, search_filters)
            if not snapshot and not should_notify and not existing_etapa:
                continue

            if existing_etapa and existing_etapa in kanban_columns:
                status_col = existing_etapa
            elif should_notify or snapshot:
                status_col = "📥 Bandeja Notificados (Discord)"
            else:
                status_col = "❌ Rechazado"

            if status_col not in kanban_columns:
                kanban_columns[status_col] = []

            post_date_str = snapshot.created_at.strftime("%Y-%m-%d") if (snapshot and snapshot.created_at) else ""
            dias_postulado = (today_date - snapshot.created_at.date()).days if (snapshot and snapshot.created_at) else 0

            modality, country, origin_type = extract_modality_and_country(job.location, job.description, job.source)
            min_sal, max_sal, currency = parse_salary_details(job.salary or "")

            sal_str = "No especificado en aviso"
            if min_sal and max_sal:
                sal_str = f"{min_sal:,.0f} {currency}" if min_sal == max_sal else f"{min_sal:,.0f} - {max_sal:,.0f} {currency}"

            exp_salarial_val = existing_exp_sal or ""

            # ─── Bloque PDF: UNC path directo, sin copia, sin embed ──────────
            if snapshot and snapshot.pdf_path and os.path.exists(snapshot.pdf_path):
                pdf_path_obj = Path(snapshot.pdf_path)
                unc_path = to_wsl_unc_path(pdf_path_obj)
                linux_path = str(pdf_path_obj.resolve())
                pdf_block = f"""> [!SUCCESS] **CV Adaptado para esta Vacante**
> 📄 Ruta del archivo: `{linux_path}`
> 🪟 Abrir desde Windows: [**{pdf_path_obj.name}**]({unc_path})"""
            else:
                pdf_block = "> [!TIP] **CV Adaptado**\n> *Sin CV generado aún para esta vacante.*"

            # ─── Notas por defecto ────────────────────────────────────────────
            default_notes_body = f"""> [!TIP] **Seguimiento & Preparación**
> - **Contacto Reclutador:** *(Ingresa nombre / email / LinkedIn del reclutador)*
> - [ ] **Guía de Entrevista Técnica:** `python -m src.cli interview {job.id}`

#### 📞 1. Primer Contacto (HR / Recruiter Call)
- **Fecha:** 
- **Contacto:** 
- **Preguntas sobre experiencia / motivo de salida:**
  - 

#### 🧠 2. Prueba / Entrevista Psicológica
- **Fecha:** 
- **Evaluador:** 
- **Comentarios:**
  - 

#### 💻 3. Entrevista Técnica / System Design
- **Fecha:** 
- **Entrevistadores:** 
- **Preguntas Técnicas & Arquitectura:**
  - 

#### 👔 4. Reunión con Jefatura / Manager de Área
- **Fecha:** 
- **Asistentes:** 
- **Alineación de Expectativas / Desafíos:**
  - 

#### 📜 5. Carta Oferta
- **Fecha Recepción:** 
- **Monto Oferta Gross/Net:** 
- **Beneficios & Decisión:**
  - 

---

### 🤖 Feedback & Retroalimentación de IA (Post-Entrevista)
> [!NOTE] **Análisis de Desempeño por IA**
> *(Escribe arriba tus preguntas y respuestas, luego ejecuta el evaluador de IA para recomendaciones de mejora).*"""

            active_notes_body = existing_notes_body if existing_notes_body else default_notes_body

            score_badge = f"🟩 {match.score:.0f}% Fit" if match.score >= 80 else f"🟨 {match.score:.0f}% Fit"
            loc_icon = "🇨🇱" if country == "Chile" else "🌎"

            # ─── Contenido de la ficha (orden: Frontmatter → Resumen oferta →
            #     Justificación FIT + GAPS → CV → Notas → Descripción) ─────────
            md_content = f"""---
job_id: {job.id}
empresa: "{job.company}"
cargo: "{job.title}"
fuente: "{job.source.upper()}"
url: "{job.url}"
etapa: "{status_col}"
modalidad: "{modality}"
pais: "{country}"
origen_tipo: "{origin_type}"
fecha_publicacion: "{pub_date_str}"
fecha_postulacion: "{post_date_str}"
dias_desde_postulacion: {dias_postulado}
score_fit: {match.score:.1f}
tier: {match.tier}
expectativa_salarial: "{exp_salarial_val}"
oferta_rango: "{sal_str}"
moneda: "{currency or 'CLP'}"
contacto_reclutador: "{existing_contacto}"
pdf_path: "{snapshot.pdf_path if snapshot else ''}"
tags:
  - job-fit
  - {sanitize_filename(job.source).lower()}
  - {sanitize_filename(country).lower()}
---

# {job.title} @ {job.company}

> **{loc_icon} {job.location}** · **{modality}** · {score_badge} (Tier {match.tier}) · [{job.source.upper()}]({job.url})
> 💵 Sueldo oferta: **{sal_str}** · 💰 Expectativa: {exp_salarial_val or '*(por definir)*'} · 📅 Publicado: {pub_date_str}

---

### 📄 Resumen de la Oferta

<details>
<summary>Desplegar aviso completo</summary>

{job.description}

</details>

---

### 🎯 Justificación del Fit (LLM)
> [!INFO] **Análisis Estratégico de Perfil**
> {match.rationale}

### ⚠️ Gaps & Habilidades Faltantes
`{match.missing_keywords}`

---

### 📎 CV Generado

{pdf_block}

---

### 🗣️ Bitácora de Entrevistas & Notas
{active_notes_body}
"""

            card_path.write_text(md_content, encoding="utf-8")
            summary["cards_created"] += 1
            card_title = f"{score_badge} | {job.company} - {job.title}"
            card_ref = f"[[jobs/{file_name}|{card_title}]]"
            kanban_columns[status_col].append(card_ref)

    # ─── 5. Reconstruir el Kanban incluyendo tarjetas ya existentes ───────────
    # Re-leer fichas existentes en jobs/ para no perder tarjetas anteriores del Kanban
    existing_cards_by_etapa: dict[str, list[str]] = {}
    for md_file in jobs_dir.glob("*.md"):
        if md_file.name in {fn for refs in [active_card_filenames] for fn in refs}:
            try:
                text = md_file.read_text(encoding="utf-8")
                m_etapa = re.search(r'^etapa:\s*"(.*?)"', text, re.MULTILINE)
                m_score = re.search(r'^score_fit:\s*([\d.]+)', text, re.MULTILINE)
                m_empresa = re.search(r'^empresa:\s*"(.*?)"', text, re.MULTILINE)
                m_cargo = re.search(r'^cargo:\s*"(.*?)"', text, re.MULTILINE)
                if m_etapa and m_empresa and m_cargo:
                    etapa = m_etapa.group(1)
                    score = float(m_score.group(1)) if m_score else 80.0
                    score_b = f"🟩 {score:.0f}% Fit" if score >= 80 else f"🟨 {score:.0f}% Fit"
                    card_title = f"{score_b} | {m_empresa.group(1)} - {m_cargo.group(1)}"
                    card_ref = f"[[jobs/{md_file.name}|{card_title}]]"
                    existing_cards_by_etapa.setdefault(etapa, [])
                    # Agregar solo si no fue recién generada (evita duplicados)
                    already_in_col = any(md_file.name in ref for ref in kanban_columns.get(etapa, []))
                    if not already_in_col:
                        existing_cards_by_etapa[etapa].append(card_ref)
            except Exception:
                pass

    for etapa, refs in existing_cards_by_etapa.items():
        if etapa not in kanban_columns:
            kanban_columns[etapa] = []
        kanban_columns[etapa].extend(refs)

    # ─── 6. Escribir Tablero_Postulaciones.md ─────────────────────────────────
    kanban_lines = ["---", "kanban-plugin: basic", "---", ""]
    for col_name, cards in kanban_columns.items():
        kanban_lines.append(f"## {col_name}")
        kanban_lines.append("")
        kanban_lines.extend(f"- [ ] {card}" for card in cards) if cards else kanban_lines.append("- [ ] ")
        kanban_lines.append("")

    kanban_path.write_text("\n".join(kanban_lines), encoding="utf-8")

    # ─── 7. Actualizar marca de agua ──────────────────────────────────────────
    set_obsidian_last_sync(sync_start)

    logger.info(f"Sync Obsidian completada: {summary['cards_created']} fichas nuevas exportadas.")
    return summary


if __name__ == "__main__":
    sync_obsidian_vault()
