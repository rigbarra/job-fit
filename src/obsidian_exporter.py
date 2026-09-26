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

_BANDEJA_COL = "📥 Bandeja Notificados (Discord)"
_KANBAN_HEADER = "---\nkanban-plugin: basic\n---\n"


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


def to_wsl_unc_path(linux_path: Path, distro: str = "Debian") -> str:
    r"""Convierte ruta Linux/WSL a file:// URI (file://///wsl$/Debian/...) para abrir desde Obsidian."""
    resolved = str(linux_path.resolve())
    return f"file://///wsl$/{distro}{resolved}"


def to_file_url(path: Path) -> str:
    """Convierte /mnt/c/... en file:///C:/... para Obsidian Windows. Linux → as_uri()."""
    p_str = str(path.resolve()).replace("\\", "/")
    if p_str.startswith("/mnt/") and len(p_str) > 6 and p_str[6] == "/":
        drive = p_str[5].upper()
        rest = p_str[6:]
        return f"file:///{drive}:{rest}"
    return path.resolve().as_uri()


def to_display_path(path: Path) -> str:
    r"""Convierte /mnt/c/... → C:\... para mostrar, o mantiene ruta Linux."""
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
    clean = re.sub(r'[^\w\s-]', '', text or '').strip()
    clean = re.sub(r'[\s-]+', '_', clean)
    return clean[:50]


def is_job_notified(job: Job, match: MatchResult, notification_rules: dict, search_filters: dict) -> bool:
    min_score = float(notification_rules.get("min_score_to_notify", 75.0))
    if match.score < min_score:
        return False
    is_local = is_local_location(job.location)
    group_key = "national" if is_local else "international"
    if not bool(notification_rules.get(group_key, {}).get(f"allow_tier_{match.tier}", False)):
        return False
    excluded_comps = search_filters.get("excluded_companies", [])
    if any(ex.lower() in (job.company or "").lower() for ex in excluded_comps):
        return False
    return True


def _resolve_vault(base_dir: Path | None, obsidian_cfg: dict) -> Path:
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


def _read_kanban_filenames(kanban_path: Path) -> set[str]:
    """Devuelve el conjunto de filenames referenciados en el Kanban (cualquier columna)."""
    if not kanban_path.exists():
        return set()
    try:
        return set(re.findall(r'\[\[jobs/([^\|\]]+\.md)', kanban_path.read_text(encoding="utf-8")))
    except Exception as ex:
        logger.warning(f"Error leyendo Kanban: {ex}")
        return set()


def _purge_orphaned_md(jobs_dir: Path, active_filenames: set[str]) -> set[int]:
    """Elimina .md huérfanos (borrados del Kanban) y su PDF generado."""
    deleted_ids: set[int] = set()
    if not jobs_dir.exists():
        return deleted_ids
    for md_file in jobs_dir.glob("*.md"):
        if md_file.name in active_filenames:
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            m = re.search(r'^job_id:\s*(\d+)', text, re.MULTILINE)
            if m:
                deleted_ids.add(int(m.group(1)))
            pdf_m = re.search(r'^pdf_path:\s*"([^"]+)"', text, re.MULTILINE)
            if pdf_m:
                pdf_file = Path(pdf_m.group(1))
                if pdf_file.exists():
                    pdf_file.unlink()
                    logger.info(f"Purgado PDF: {pdf_file.name}")
            md_file.unlink()
            logger.info(f"Purgada ficha: {md_file.name}")
        except Exception as ex:
            logger.warning(f"Error purgando {md_file.name}: {ex}")
    return deleted_ids


def _insert_cards_into_kanban(kanban_path: Path, new_card_refs: list[str]) -> None:
    """Inserta nuevas tarjetas en la columna Bandeja del Kanban SIN tocar el resto."""
    if not new_card_refs:
        return

    if kanban_path.exists():
        lines = kanban_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = [*_KANBAN_HEADER.splitlines(), "", f"## {_BANDEJA_COL}", ""]

    # Buscar la línea del encabezado de la columna Bandeja
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {_BANDEJA_COL}":
            # Insertar justo después del encabezado (y línea vacía opcional)
            insert_idx = i + 1
            if insert_idx < len(lines) and lines[insert_idx].strip() == "":
                insert_idx += 1
            break

    if insert_idx is None:
        # La columna no existe: crearla al final
        lines += ["", f"## {_BANDEJA_COL}", ""]
        insert_idx = len(lines)

    for ref in reversed(new_card_refs):
        lines.insert(insert_idx, f"- [ ] {ref}")

    kanban_path.write_text("\n".join(lines), encoding="utf-8")


def _retrofit_kanban_dates(kanban_path: Path) -> int:
    """Asegura que todas las tarjetas en el Kanban incluyan @{YYYY-MM-DD} para habilitar ordenar por fecha."""
    if not kanban_path.exists():
        return 0
    text = kanban_path.read_text(encoding="utf-8")
    updated = re.sub(r'(\[\[jobs/(\d{4}-\d{2}-\d{2})_[^\]]+\]\])(?!\s*@\{)', r'\1 @{\2}', text)
    if updated != text:
        kanban_path.write_text(updated, encoding="utf-8")
        return len(re.findall(r'\[\[jobs/\d{4}-\d{2}-\d{2}_[^\]]+\]\]\s*@\{', updated))
    return 0


def _update_existing_cards(jobs_dir: Path, today_date: date) -> int:
    """
    Actualiza campos calculados y nuevos en las fichas existentes sin tocar notas ni contenido del usuario:
    - dias_desde_publicacion = (today - fecha_publicacion)
    - dias_desde_postulacion = (today - fecha_postulacion) si está definida, o null
    - notas = campo de texto en propiedades inicializado si no existe
    - expectativa_salarial = normalizado a numérico (int o null)
    """
    updated_count = 0
    if not jobs_dir.exists():
        return updated_count

    for md_file in jobs_dir.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            m = re.match(r"^(---\n)(.*?\n)(---\n.*)$", content, re.DOTALL)
            if not m:
                continue
            pre, fm, post = m.groups()
            orig_fm = fm

            # fecha_publicacion & dias_desde_publicacion
            pub_m = re.search(r'^fecha_publicacion:\s*["\']?(\d{4}-\d{2}-\d{2})', fm, re.M)
            pub_date = date.fromisoformat(pub_m.group(1)) if pub_m else None
            dias_pub = (today_date - pub_date).days if pub_date else 0

            if re.search(r"^dias_desde_publicacion:", fm, re.M):
                fm = re.sub(r"^dias_desde_publicacion:.*$", f"dias_desde_publicacion: {dias_pub}", fm, flags=re.M)
            else:
                fm = re.sub(r"^(fecha_publicacion:.*?)$", f"\\1\ndias_desde_publicacion: {dias_pub}", fm, flags=re.M)

            # fecha_postulacion
            post_m = re.search(r'^fecha_postulacion:\s*["\']?([^"\'\r\n]*)', fm, re.M)
            post_str = post_m.group(1).strip() if post_m else ""

            # Si estaba en bandeja y era idéntica a fecha_publicacion, limpiar para que el usuario la complete
            etapa_m = re.search(r'^etapa:\s*["\']?([^"\'\r\n]*)', fm, re.M)
            etapa_str = etapa_m.group(1).strip() if etapa_m else ""
            if pub_date and post_str == pub_date.isoformat() and _BANDEJA_COL in etapa_str:
                post_str = ""
                fm = re.sub(r"^fecha_postulacion:.*$", 'fecha_postulacion: ""', fm, flags=re.M)

            try:
                post_date = date.fromisoformat(post_str) if post_str else None
            except ValueError:
                post_date = None

            dias_post = (today_date - post_date).days if post_date else "null"
            if re.search(r"^dias_desde_postulacion:", fm, re.M):
                fm = re.sub(r"^dias_desde_postulacion:.*$", f"dias_desde_postulacion: {dias_post}", fm, flags=re.M)
            else:
                fm = re.sub(r"^(fecha_postulacion:.*?)$", f"\\1\ndias_desde_postulacion: {dias_post}", fm, flags=re.M)

            # notas
            if not re.search(r"^notas:", fm, re.M):
                if re.search(r"^tier:", fm, re.M):
                    fm = re.sub(r"^(tier:.*?)$", '\\1\nnotas: ""', fm, flags=re.M)
                else:
                    fm += 'notas: ""\n'

            # expectativa_salarial (numérico: entero o null)
            sal_m = re.search(r'^expectativa_salarial:\s*["\']?([^"\'\r\n]*)', fm, re.M)
            if sal_m:
                val = sal_m.group(1).strip()
                digits = re.sub(r"[^\d]", "", val)
                new_val = digits if digits else "null"
                fm = re.sub(r"^expectativa_salarial:.*$", f"expectativa_salarial: {new_val}", fm, flags=re.M)

            if fm != orig_fm:
                md_file.write_text(f"{pre}{fm}{post}", encoding="utf-8")
                updated_count += 1
        except Exception as ex:
            logger.warning(f"Error actualizando ficha {md_file.name}: {ex}")

    return updated_count


def sync_obsidian_vault(base_dir: Path | None = None) -> dict:
    """
    Sincronización incremental con Obsidian.

    Regla fundamental: el Kanban nunca se reconstruye desde cero.
    Solo se agregan tarjetas NUEVAS a la columna Bandeja.
    Las tarjetas existentes (incluyendo las movidas por el usuario) jamás se tocan.
    """
    config = load_config()
    obsidian_cfg = config.get("obsidian", {})
    search_filters = config.get("search_filters", {})
    notification_rules = config.get("notification_rules", {})

    target_dir = _resolve_vault(base_dir, obsidian_cfg)
    jobs_dir = target_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    kanban_path = target_dir / "Tablero_Postulaciones.md"
    auto_clean_orphans = bool(obsidian_cfg.get("auto_clean_orphans", True))
    summary = {"total_jobs": 0, "cards_created": 0, "cards_updated": 0, "kanban_file": str(kanban_path)}

    # ─── 1. Leer filenames activos en el Kanban ───────────────────────────────
    active_filenames = _read_kanban_filenames(kanban_path)

    # ─── 2. Purgar .md huérfanos (tarjeta borrada en el Kanban) ──────────────
    deleted_job_ids: set[int] = set()
    if auto_clean_orphans and active_filenames:
        deleted_job_ids = _purge_orphaned_md(jobs_dir, active_filenames)

    today_date = date.today()

    # Retrofit de tarjetas en Kanban para asegurar fecha @{YYYY-MM-DD} (ordenar por fecha)
    _retrofit_kanban_dates(kanban_path)

    # Actualizar fichas existentes (días transcurridos, expectativa numérica, campo notas)
    summary["cards_updated"] = _update_existing_cards(jobs_dir, today_date)

    # ─── 3. Ventana temporal incremental ─────────────────────────────────────
    last_sync = get_obsidian_last_sync()
    if last_sync is None:
        cutoff_str = obsidian_cfg.get("start_cutoff")
        try:
            last_sync = datetime.strptime(cutoff_str, "%Y-%m-%d %H:%M:%S") if cutoff_str else datetime(2026, 9, 4, 21, 10, 0)
        except Exception:
            last_sync = datetime(2026, 9, 4, 21, 10, 0)
    if last_sync.tzinfo is not None:
        last_sync = last_sync.replace(tzinfo=None)

    sync_start = datetime.now(tz=UTC).replace(tzinfo=None)

    # ─── 4. Consultar vacantes nuevas desde last_sync ────────────────────────
    new_card_refs: list[str] = []

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

            if hasattr(match.created_at, "date"):
                pub_date = match.created_at.date()
            elif isinstance(match.created_at, date):
                pub_date = match.created_at
            else:
                pub_date = today_date
            pub_date_str = pub_date.strftime("%Y-%m-%d")
            clean_company = sanitize_filename(job.company)
            clean_title = sanitize_filename(job.title)
            file_name = f"{pub_date_str}_{clean_company}_{clean_title}.md"
            card_path = jobs_dir / file_name

            # Si ya existe en el Kanban, NUNCA la tocamos (ni el .md ni su posición)
            if file_name in active_filenames:
                continue

            should_notify = is_job_notified(job, match, notification_rules, search_filters)
            if not snapshot and not should_notify:
                continue

            modality, country, origin_type = extract_modality_and_country(job.location, job.description, job.source)
            min_sal, max_sal, currency = parse_salary_details(job.salary or "")
            sal_str = "No especificado en aviso"
            if min_sal and max_sal:
                sal_str = f"{min_sal:,.0f} {currency}" if min_sal == max_sal else f"{min_sal:,.0f} - {max_sal:,.0f} {currency}"

            dias_pub = (today_date - pub_date).days
            post_date_str = ""  # El usuario completa este campo en Obsidian al postular
            dias_post_str = "null"

            if snapshot and snapshot.pdf_path and os.path.exists(snapshot.pdf_path):
                pdf_path_obj = Path(snapshot.pdf_path)
                unc_path = to_wsl_unc_path(pdf_path_obj)
                linux_path = str(pdf_path_obj.resolve())
                pdf_block = f"""> [!SUCCESS] **CV Adaptado para esta Vacante**
> 📄 Ruta del archivo: `{linux_path}`
> 🪟 Abrir desde Windows: [**{pdf_path_obj.name}**]({unc_path})"""
            else:
                pdf_block = "> [!TIP] **CV Adaptado**\n> *Sin CV generado aún para esta vacante.*"

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

            score_badge = f"🟩 {match.score:.0f}% Fit" if match.score >= 80 else f"🟨 {match.score:.0f}% Fit"
            loc_icon = "🇨🇱" if country == "Chile" else "🌎"

            md_content = f"""---
job_id: {job.id}
empresa: "{job.company}"
cargo: "{job.title}"
fuente: "{job.source.upper()}"
url: "{job.url}"
etapa: "{_BANDEJA_COL}"
modalidad: "{modality}"
pais: "{country}"
origen_tipo: "{origin_type}"
fecha_publicacion: "{pub_date_str}"
fecha_postulacion: "{post_date_str}"
dias_desde_publicacion: {dias_pub}
dias_desde_postulacion: {dias_post_str}
score_fit: {match.score:.1f}
tier: {match.tier}
notas: ""
expectativa_salarial: null
oferta_rango: "{sal_str}"
moneda: "{currency or 'CLP'}"
contacto_reclutador: ""
pdf_path: "{snapshot.pdf_path if snapshot else ''}"
tags:
  - job-fit
  - {sanitize_filename(job.source).lower()}
  - {sanitize_filename(country).lower()}
---

# {job.title} @ {job.company}

> **{loc_icon} {job.location}** · **{modality}** · {score_badge} (Tier {match.tier}) · [{job.source.upper()}]({job.url})
> 💵 Sueldo oferta: **{sal_str}** · 📅 Publicado: {pub_date_str} ({dias_pub} días)

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
{default_notes_body}
"""
            card_path.write_text(md_content, encoding="utf-8")
            summary["cards_created"] += 1
            card_ref = f"[[jobs/{file_name}|{score_badge} | {job.company} - {job.title}]] @{{{pub_date_str}}}"
            new_card_refs.append(card_ref)

    # ─── 5. Insertar solo las tarjetas nuevas en el Kanban ────────────────────
    # El Kanban existente NO se toca. Solo se agregan refs nuevas a la Bandeja.
    _insert_cards_into_kanban(kanban_path, new_card_refs)

    # ─── 6. Actualizar marca de agua ─────────────────────────────────────────
    set_obsidian_last_sync(sync_start)

    logger.info(f"Sync Obsidian: {summary['cards_created']} fichas nuevas.")
    return summary


if __name__ == "__main__":
    sync_obsidian_vault()
