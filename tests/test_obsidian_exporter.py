from pathlib import Path

from src.obsidian_exporter import (
    sanitize_filename,
    sync_obsidian_vault,
    to_display_path,
    to_file_url,
)


def test_to_file_url_and_display():
    """Valida la conversión de rutas WSL y nativas a URLs y rutas legibles."""
    # Ruta WSL simulada
    wsl_p = Path("/mnt/c/job-fit-obsidian/CVs/cv.pdf")
    assert to_file_url(wsl_p) == "file:///C:/job-fit-obsidian/CVs/cv.pdf"
    assert to_display_path(wsl_p) == "C:\\job-fit-obsidian\\CVs\\cv.pdf"

    # Ruta nativa Linux
    linux_p = Path("/tmp/test_dir/file.txt")
    assert to_file_url(linux_p).startswith("file://")
    assert to_display_path(linux_p) == str(linux_p.resolve())


def test_sanitize_filename():
    """Valida la sanitización de nombres de archivo entre plataformas."""
    assert sanitize_filename("Senior Data Engineer (AWS/GCP)!") == "Senior_Data_Engineer_AWSGCP"
    assert sanitize_filename("Empresa  & Co. -- 2026") == "Empresa_Co_2026"


def test_sync_obsidian_vault_creates_structure(tmp_path):
    """Valida que sync_obsidian_vault inicialice la estructura mínima (sin CVs/, solo jobs/)."""
    res = sync_obsidian_vault(base_dir=tmp_path)
    assert (tmp_path / "Tablero_Postulaciones.md").exists()
    assert (tmp_path / "jobs").is_dir()
    assert not (tmp_path / "CVs").exists(), "CVs/ no debe crearse — links UNC directos al PDF original"
    assert res["kanban_file"] == str(tmp_path / "Tablero_Postulaciones.md")


def test_to_wsl_unc_path():
    """Valida la conversión de ruta Linux a file:// URI para abrir desde Obsidian."""
    from src.obsidian_exporter import to_wsl_unc_path
    p = Path("/home/user/projects/job-fit/data/generated_cvs/cv.pdf")
    unc = to_wsl_unc_path(p, distro="Debian")
    assert unc.startswith("file://///wsl$/Debian/")
    assert "/home/user" in unc


def test_retrofit_kanban_dates(tmp_path):
    """Valida que _retrofit_kanban_dates agregue @{YYYY-MM-DD} a tarjetas existentes."""
    from src.obsidian_exporter import _retrofit_kanban_dates
    kanban_file = tmp_path / "Tablero_Postulaciones.md"
    content = (
        "---\nkanban-plugin: basic\n---\n\n"
        "## 📥 Bandeja Notificados (Discord)\n\n"
        "- [ ] [[jobs/2026-09-25_Sermaluc_Ingeniero_AWS.md|🟩 92% Fit | Sermaluc - AWS]]\n"
        "- [ ] [[jobs/2026-09-26_FullStack_AI.md|🟩 95% Fit | FullStack]] @{2026-09-26}\n"
    )
    kanban_file.write_text(content, encoding="utf-8")
    _retrofit_kanban_dates(kanban_file)

    updated = kanban_file.read_text(encoding="utf-8")
    assert "- [ ] [[jobs/2026-09-25_Sermaluc_Ingeniero_AWS.md|🟩 92% Fit | Sermaluc - AWS]] @{2026-09-25}" in updated
    assert "- [ ] [[jobs/2026-09-26_FullStack_AI.md|🟩 95% Fit | FullStack]] @{2026-09-26}" in updated
    # Idempotencia: no debe duplicar @{...}
    _retrofit_kanban_dates(kanban_file)
    assert updated.count("@{2026-09-25}") == 1


def test_update_existing_cards(tmp_path):
    """Valida que _update_existing_cards actualice días, notas y expectativa salarial numérica."""
    from datetime import date
    from src.obsidian_exporter import _update_existing_cards

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    card_file = jobs_dir / "2026-09-20_Empresa_Cargo.md"

    card_content = (
        "---\n"
        "job_id: 100\n"
        "fecha_publicacion: \"2026-09-20\"\n"
        "fecha_postulacion: \"2026-09-22\"\n"
        "dias_desde_postulacion: 0\n"
        "tier: 1\n"
        "expectativa_salarial: \"2.800.000\"\n"
        "---\n\n"
        "# Cargo @ Empresa\n"
        "Notas personalizadas del usuario que NO deben borrarse.\n"
    )
    card_file.write_text(card_content, encoding="utf-8")

    fixed_today = date(2026, 9, 26)
    updated = _update_existing_cards(jobs_dir, fixed_today)
    assert updated == 1

    text = card_file.read_text(encoding="utf-8")
    assert "dias_desde_publicacion: 6" in text
    assert "dias_desde_postulacion: 4" in text
    assert "expectativa_salarial: 2800000" in text
    assert 'notas: ""' in text
    assert "Notas personalizadas del usuario que NO deben borrarse." in text


def test_archive_ghosted_cards(tmp_path):
    """Valida que _archive_ghosted_cards mueva solo avisos aplicados > 60 días a Ghosted."""
    from datetime import date
    from src.obsidian_exporter import _archive_ghosted_cards, _GHOSTED_COL

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Archivo aplicado antiguo (> 60 días)
    old_applied_md = jobs_dir / "2026-07-01_EmpresaA_Data_Eng.md"
    old_applied_md.write_text('---\netapa: "📤 Aplicado"\n---\n# Postulación Antigua', encoding="utf-8")

    # 2. Archivo aplicado reciente (< 60 días)
    recent_applied_md = jobs_dir / "2026-09-20_EmpresaB_Analytics.md"
    recent_applied_md.write_text('---\netapa: "📤 Aplicado"\n---\n# Postulación Reciente', encoding="utf-8")

    # 3. Tablero Kanban con diferentes columnas
    kanban_file = tmp_path / "Tablero_Postulaciones.md"
    kanban_content = (
        "---\nkanban-plugin: basic\n---\n\n"
        "## 📥 Bandeja Notificados (Discord)\n\n"
        "- [ ] [[jobs/2026-06-01_Bandeja_Vieja.md|Bandeja Vieja]] @{2026-06-01}\n\n"
        "## 📤 Aplicado\n\n"
        "- [ ] [[jobs/2026-07-01_EmpresaA_Data_Eng.md|Empresa A]] @{2026-07-01}\n"
        "- [ ] [[jobs/2026-09-20_EmpresaB_Analytics.md|Empresa B]] @{2026-09-20}\n\n"
        "## 👻 Ghosted / Sin Respuesta\n\n"
        "## ❌ Rechazado\n\n"
        "- [ ] [[jobs/2026-06-01_Rechazo_Viejo.md|Rechazado]] @{2026-06-01}\n"
    )
    kanban_file.write_text(kanban_content, encoding="utf-8")

    fixed_today = date(2026, 9, 26)
    moved_count = _archive_ghosted_cards(kanban_file, jobs_dir, fixed_today, threshold_days=60)

    assert moved_count == 1

    kanban_text = kanban_file.read_text(encoding="utf-8")
    # Empresa A (> 60 días) debe estar bajo Ghosted
    ghosted_section = kanban_text.split("## 👻 Ghosted / Sin Respuesta")[1].split("## ❌ Rechazado")[0]
    assert "2026-07-01_EmpresaA_Data_Eng.md" in ghosted_section

    # Empresa B (< 60 días) debe permanecer bajo Aplicado
    aplicado_section = kanban_text.split("## 📤 Aplicado")[1].split("## 👻 Ghosted")[0]
    assert "2026-09-20_EmpresaB_Analytics.md" in aplicado_section
    assert "2026-07-01_EmpresaA_Data_Eng.md" not in aplicado_section

    # Bandeja y Rechazado no deben moverse
    bandeja_section = kanban_text.split("## 📥 Bandeja Notificados (Discord)")[1].split("## 📤 Aplicado")[0]
    assert "2026-06-01_Bandeja_Vieja.md" in bandeja_section

    rechazado_section = kanban_text.split("## ❌ Rechazado")[1]
    assert "2026-06-01_Rechazo_Viejo.md" in rechazado_section

    # Validar que la propiedad etapa se actualizó en el archivo .md
    assert f'etapa: "{_GHOSTED_COL}"' in old_applied_md.read_text(encoding="utf-8")


