import os
import re
import logging
from pathlib import Path
from datetime import datetime, date
from sqlmodel import Session, select

from config.settings import load_config, settings
from src.database.repository import engine
from src.database.models import Job, MatchResult, CVSnapshot
from src.agent.filter import extract_modality_and_country, parse_salary_details
from src.main import is_local_location

logger = logging.getLogger("obsidian-exporter")

def get_windows_downloads_dir() -> Path | None:
    """Detecta dinámicamente la carpeta Descargas del usuario activo en Windows sin quemar nombres."""
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        drive_letter = userprofile[0].lower()
        subpath = userprofile[2:].replace("\\", "/")
        p = Path(f"/mnt/{drive_letter}{subpath}/Downloads")
        if p.exists():
            return p

    c_users = Path("/mnt/c/Users")
    if c_users.exists():
        excluded = {"default", "default user", "public", "all users"}
        for user_dir in c_users.iterdir():
            if user_dir.is_dir() and user_dir.name.lower() not in excluded:
                dl = user_dir / "Downloads"
                if dl.exists():
                    return dl
    return None


def to_file_url(path: Path) -> str:
    """Convierte una ruta de archivo en una URL file:///... válida para Windows/WSL o Linux/macOS."""
    p_str = str(path.resolve()).replace("\\", "/")
    if p_str.startswith("/mnt/") and len(p_str) > 6 and p_str[6] == "/":
        drive = p_str[5].upper()
        rest = p_str[6:]
        return f"file:///{drive}:{rest}"
    return path.resolve().as_uri()


def to_display_path(path: Path) -> str:
    r"""Convierte una ruta WSL (/mnt/c/...) en una ruta legible de Windows (C:\...), o mantiene la ruta nativa."""
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
    """Limpia caracteres especiales para generar nombres de archivo válidos en cualquier S.O."""
    clean = re.sub(r'[^\w\s-]', '', text or '').strip()
    clean = re.sub(r'[\s-]+', '_', clean)
    return clean[:50]


def is_job_notified(job: Job, match: MatchResult, notification_rules: dict, search_filters: dict) -> bool:
    """Verifica si la vacante cumple exactamente con las reglas para ser notificada a Discord."""
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


def sync_obsidian_vault(base_dir: Path | None = None) -> dict:
    """
    Sincroniza la base de datos SQLite con el Vault de Obsidian.
    Escribe únicamente las vacantes que NOTIFICAN A DISCORD (Score >= 75, Tier permitido) o las APLICADAS.
    Respeta las eliminaciones manuales del usuario y purga archivos huérfanos.
    """
    config = load_config()
    obsidian_cfg = config.get("obsidian", {})
    search_filters = config.get("search_filters", {})
    notification_rules = config.get("notification_rules", {})

    # Punto de corte temporal configurable (default a hace 24 horas si no existe)
    cutoff_str = obsidian_cfg.get("start_cutoff")
    if cutoff_str:
        try:
            start_cutoff = datetime.strptime(cutoff_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            start_cutoff = datetime(2026, 9, 4, 21, 10, 0)
    else:
        start_cutoff = datetime(2026, 9, 4, 21, 10, 0)

    configured_vault = obsidian_cfg.get("vault_path")
    if base_dir:
        target_dir = base_dir
    elif configured_vault:
        cfg_p = Path(configured_vault)
        if str(cfg_p).startswith("/mnt/") and not Path("/mnt/c").exists():
            target_dir = Path(settings.project_root) / "output" / "obsidian"
        else:
            target_dir = cfg_p
    elif Path("/mnt/c").exists():
        target_dir = Path("/mnt/c/job-fit-obsidian")
    else:
        target_dir = Path(settings.project_root) / "output" / "obsidian"

    jobs_dir = target_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    cvs_dir = target_dir / "CVs"
    cvs_dir.mkdir(parents=True, exist_ok=True)

    kanban_path = target_dir / "Tablero_Postulaciones.md"
    auto_clean_orphans = bool(obsidian_cfg.get("auto_clean_orphans", True))
    summary = {"total_jobs": 0, "cards_created": 0, "kanban_file": str(kanban_path)}

    # 1. Leer qué tarjetas están activas en el tablero Kanban
    active_card_filenames = set()
    deleted_job_ids = set()
    if kanban_path.exists():
        try:
            with open(kanban_path, "r", encoding="utf-8") as kf:
                k_text = kf.read()
                for m in re.finditer(r'\[\[jobs/([^\|\]]+\.md)', k_text):
                    active_card_filenames.add(m.group(1))
        except Exception as ex:
            logger.warning(f"Error leyendo tablero Kanban: {ex}")

    # 2. Purgar archivos huérfanos si el usuario eliminó una tarjeta del tablero
    if auto_clean_orphans and active_card_filenames:
        for md_file in jobs_dir.glob("*.md"):
            if md_file.name not in active_card_filenames:
                try:
                    with open(md_file, "r", encoding="utf-8") as mf:
                        m_text = mf.read()
                        jid_match = re.search(r'^job_id:\s*(\d+)', m_text, re.MULTILINE)
                        if jid_match:
                            deleted_job_ids.add(int(jid_match.group(1)))
                        pdf_match = re.search(r'\[\[CVs/([^\|\]]+\.pdf)', m_text)
                        if pdf_match:
                            orphan_pdf = cvs_dir / pdf_match.group(1)
                            if orphan_pdf.exists():
                                orphan_pdf.unlink()
                                logger.info(f"Purgado CV PDF huérfano de tarjeta eliminada: {orphan_pdf.name}")
                    md_file.unlink()
                    logger.info(f"Purgada ficha huérfana de tarjeta eliminada: {md_file.name}")
                except Exception as ex:
                    logger.warning(f"Error purgando archivo huérfano {md_file.name}: {ex}")

    with Session(engine) as session:
        applied_job_ids_subquery = select(CVSnapshot.job_id).where(CVSnapshot.created_at >= start_cutoff)
        results = session.exec(
            select(Job, MatchResult)
            .outerjoin(MatchResult, Job.id == MatchResult.job_id)
            .where(
                (Job.id.in_(applied_job_ids_subquery)) |
                ((MatchResult.score >= 75.0) & (MatchResult.created_at >= start_cutoff))
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
                    rationale = "Postulación previa importada de Notion."
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

            # Preservar etapa si la tarjeta ya existe en Obsidian
            existing_etapa = None
            existing_exp_sal = ""
            existing_contacto = ""
            existing_notes_body = None

            if card_path.exists():
                try:
                    with open(card_path, "r", encoding="utf-8") as f:
                        old_text = f.read()
                        
                        m_etapa = re.search(r'^etapa:\s*"(.*?)"', old_text, re.MULTILINE)
                        if m_etapa and m_etapa.group(1) in kanban_columns:
                            existing_etapa = m_etapa.group(1)
                            
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
                    logger.warning(f"Error leyendo archivo existente {file_name}: {ex}")

            should_notify = is_job_notified(job, match, notification_rules, search_filters)
            if not snapshot and not should_notify and not existing_etapa:
                continue

            # Determinar estado de la columna:
            # 1. Si la tarjeta ya existe en el Vault de Obsidian, PRESERVAR estrictamente la etapa/columna elegida por el usuario.
            # 2. Toda oferta nueva notificada NACE SIEMPRE en "📥 Bandeja Notificados (Discord)".
            if existing_etapa:
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
                if min_sal == max_sal:
                    sal_str = f"{min_sal:,.0f} {currency}"
                else:
                    sal_str = f"{min_sal:,.0f} - {max_sal:,.0f} {currency}"

            pdf_link = f"file://{snapshot.pdf_path}" if snapshot else ""
            exp_salarial_val = existing_exp_sal if existing_exp_sal != "" else ""

            default_notes_body = f"""> [!TIP] **Seguimiento & Preparación**
> - **Contacto Reclutador:** *(Ingresa nombre / email / LinkedIn del reclutador)*
> - [ ] **Guía de Entrevista Técnica:** Generar ejecutando en terminal `python -m src.cli interview {job.id}`

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
> *(Escribe arriba tus preguntas y respuestas, luego ejecuta el evaluador de IA para generar recomendaciones de mejora).*"""

            active_notes_body = existing_notes_body if existing_notes_body else default_notes_body

            score_badge = f"🟩 {match.score:.0f}% Fit" if match.score >= 80 else f"🟨 {match.score:.0f}% Fit"
            loc_icon = "🇨🇱" if country == "Chile" else "🌎"

            if snapshot and snapshot.pdf_path and os.path.exists(snapshot.pdf_path):
                pdf_file_name = Path(snapshot.pdf_path).name
                dest_cv_path = cvs_dir / pdf_file_name
                try:
                    import shutil
                    shutil.copy2(snapshot.pdf_path, dest_cv_path)
                except Exception as ex:
                    logger.warning(f"Error al copiar PDF a carpeta CVs: {ex}")

                win_cv_url = to_windows_file_url(dest_cv_path)
                win_folder_url = to_windows_file_url(cvs_dir)
                win_folder_display = to_windows_display_path(cvs_dir)

                pdf_block = f"""> [!SUCCESS] **CV Adaptado para esta Vacante**
> - 📂 [**Haz clic aquí para abrir la carpeta del CV**]({win_folder_url}) (`{win_folder_display}`)
> - 📄 [**Abrir archivo directamente en Lector PDF / Navegador**]({win_cv_url})
> 
> ![[CVs/{pdf_file_name}]]"""
            else:
                pdf_block = """> [!TIP] **CV Adaptado**
> *Generando análisis de CV adaptado para esta vacante...*"""

            # Contenido de la Ficha Markdown Estilo Notion Grid Panel
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

<table class="notion-property-grid">
  <tr>
    <td class="notion-prop-label">🏢 Empresa</td>
    <td class="notion-prop-value"><b>{job.company}</b></td>
    <td class="notion-prop-label">📍 Ubicación</td>
    <td class="notion-prop-value">{loc_icon} {job.location} ({country})</td>
  </tr>
  <tr>
    <td class="notion-prop-label">💻 Modalidad</td>
    <td class="notion-prop-value"><span class="badge-pill badge-modality">{modality}</span></td>
    <td class="notion-prop-label">🎯 Fit Score</td>
    <td class="notion-prop-value"><span class="badge-pill badge-score-high">{score_badge}</span> (Tier {match.tier})</td>
  </tr>
  <tr>
    <td class="notion-prop-label">💵 Sueldo Oferta</td>
    <td class="notion-prop-value"><span class="badge-pill badge-salary">{sal_str}</span></td>
    <td class="notion-prop-label">💰 Expectativa Salarial</td>
    <td class="notion-prop-value">{exp_salarial_val or '<i>*(Manual)*</i>'}</td>
  </tr>
  <tr>
    <td class="notion-prop-label">🔗 Fuente</td>
    <td class="notion-prop-value"><a href="{job.url}">{job.source.upper()}</a></td>
    <td class="notion-prop-label">👤 Reclutador</td>
    <td class="notion-prop-value">{existing_contacto or '<i>*(Por definir)*</i>'}</td>
  </tr>
</table>

{pdf_block}

---

### 🎯 Justificación del Fit (LLM)
> [!INFO] **Análisis Estratégico de Perfil**
> {match.rationale}

### ⚠️ Gaps & Habilidades Faltantes
`{match.missing_keywords}`

---

### 🗣️ Bitácora de Entrevistas & Notas
{active_notes_body}

---

### 📄 Descripción Original de la Oferta
<details>
<summary>Desplegar aviso completo</summary>

{job.description}

</details>
"""

            with open(card_path, "w", encoding="utf-8") as f:
                f.write(md_content)

            summary["cards_created"] += 1
            card_title = f"{score_badge} | {job.company} - {job.title}"
            card_ref = f"[[jobs/{file_name}|{card_title}]]"
            kanban_columns[status_col].append(card_ref)

        # Generar el archivo Kanban de Obsidian
        kanban_path = target_dir / "Tablero_Postulaciones.md"
        kanban_lines = [
            "---",
            "kanban-plugin: basic",
            "---",
            "",
        ]

        for col_name, cards in kanban_columns.items():
            kanban_lines.append(f"## {col_name}")
            kanban_lines.append("")
            if not cards:
                kanban_lines.append("- [ ] ")
            else:
                for card in cards:
                    kanban_lines.append(f"- [ ] {card}")
            kanban_lines.append("")

        with open(kanban_path, "w", encoding="utf-8") as kf:
            kf.write("\n".join(kanban_lines))

    logger.info(f"Sincronización con Obsidian completada: {summary['cards_created']} fichas exportadas.")
    return summary


if __name__ == "__main__":
    sync_obsidian_vault()
