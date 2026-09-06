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
    """Valida la conversión de ruta Linux a UNC path de Windows para abrir desde Obsidian."""
    from src.obsidian_exporter import to_wsl_unc_path
    p = Path("/home/rigbarra/projects/job-fit/data/generated_cvs/cv.pdf")
    unc = to_wsl_unc_path(p, distro="Debian")
    assert unc.startswith("\\\\wsl$\\Debian\\")
    assert "home/rigbarra" in unc or "home\\rigbarra" in unc
