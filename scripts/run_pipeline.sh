#!/usr/bin/env bash
# ==============================================================================
# Script de Ejecución Autónoma de Pipeline job-fit (Cron / WSL2 / VPS Linux)
# ==============================================================================

set -euo pipefail

# Obtener directorio raíz del proyecto
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Activar entorno virtual de Python
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    VENV_PYTHON="python3"
fi

# Directorio de logs
mkdir -p "$PROJECT_ROOT/logs"
LOG_FILE="$PROJECT_ROOT/logs/cron_pipeline.log"

echo "==================================================" >> "$LOG_FILE"
echo "Job-Fit Autonomous Scraping Run: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "==================================================" >> "$LOG_FILE"

# 1. Ejecutar scraping e ingesta secuencial
"$VENV_PYTHON" -m src.cli scrape >> "$LOG_FILE" 2>&1 || {
    echo "ERROR: Falló la ejecución del scraper en $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 1
}

# 2. Ejecutar purga de data temporal (>30 días)
"$VENV_PYTHON" -m src.cli clean --days 30 >> "$LOG_FILE" 2>&1 || true

echo "Corrida autónoma finalizada con éxito: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
