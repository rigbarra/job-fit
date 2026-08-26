# Cheat Sheet & Manual Operativo: job-fit

Guía rápida de referencia con los comandos esenciales para la operación diaria, configuración, postulación a empleos y mantenimiento del repositorio.

---

## 1. Comandos de Postulación Diaria

Todos los comandos se ejecutan desde la raíz del proyecto con el entorno virtual activo:

```bash
# Activar el entorno virtual
source .venv/bin/activate
```

### A. Postulación Completa (CV Adaptado + Score ATS)
Evalúa la vacante y genera el CV adaptado en PDF. Puedes pasar el **ID de la base de datos** o la **URL directa**:
```bash
# Por ID de base de datos
PYTHONPATH=. .venv/bin/python -m src.cli apply 39

# Por URL de Get on Board, LinkedIn o Indeed
PYTHONPATH=. .venv/bin/python -m src.cli apply "https://www.getonbrd.com/jobs/data-engineer-..."
```
* **PDF generado en:** `data/generated_cvs/`

---

### B. Preparar Guía de Entrevista Técnica
Genera un documento Markdown completo con 10-12 preguntas técnicas con código/SQL, arquitectura y 4 respuestas bajo metodología STAR específicas para la vacante:
```bash
PYTHONPATH=. .venv/bin/python -m src.cli interview 39
```
* **Guía guardada en:** `data/interviews/` (o consola)

---

### C. Generar Únicamente Carta de Presentación (Cover Letter)
```bash
PYTHONPATH=. .venv/bin/python -m src.cli cover-letter 39
```

---

### D. Recompilar Manualmente un CV editado (.tex a .pdf)
Si abriste un archivo `.tex` en VSCode dentro de `data/generated_cvs/` y le hiciste cambios manuales, recompílalo a PDF con este comando de 1 segundo:
```bash
pdflatex -output-directory=data/generated_cvs data/generated_cvs/NOMBRE_DEL_ARCHIVO.tex
```
*(Ejemplo: `pdflatex -output-directory=data/generated_cvs data/generated_cvs/CV_Rigoberto_Barra_Drimo_39_T1_es_20260825.tex`)*

---

## 2. Ingesta, Scraping y Estudio de Mercado

### Iniciar Pipeline Completo de Scraping Manual
Ejecuta el ciclo end-to-end (Scraping -> Filtro -> IA -> Discord -> Market Study):
```bash
PYTHONPATH=. .venv/bin/python -m src.cli scrape
```

### Actualizar el Estudio de Mercado en Vivo
Regenera el archivo `data/market_study/market_study.md` procesando todas las vacantes de la base de datos:
```bash
PYTHONPATH=. .venv/bin/python -m src.cli market-study
```

---

## 3. Prender, Apagar y Configurar Parámetros

### Prender o Apagar Portales de Empleo (`config/config.yaml`)
Para activar o desactivar portales según necesidad, edita la sección `sources`:
```yaml
sources:
  getonboard: true       # API REST Get on Board Chile (muy estable)
  linkedin: true         # Endpoints públicos guest de LinkedIn
  indeed: true           # Indeed (vía JobSpy)
  remotive: false        # Remotive internacional (activar para B2B / USD)
```

### Cambiar la Ventana de Días de Búsqueda (`config/config.yaml`)
```yaml
search_filters:
  max_job_age_days: 2    # 1 día (para cron diario 3x) o 2-7 días (para barridos amplios)
```

### Cambiar el Ámbito Geográfico (`config/config.yaml`)
```yaml
search_scope: "chile"    # "chile" (solo nacional), "international" (solo USD/remoto), o "all" (ambos)
```

### Cambiar el Modelo de IA / LLM (`.env`)
Para cambiar el modelo o proveedor sin tocar código, edita tu archivo `.env`:
```bash
# Opción 1: Google Gemini (Recomendado - Gratuito y Rápido)
LLM_MODEL="gemini-3.5-flash-lite"
LLM_API_KEY="AIzaSy..."

# Opción 2: Google Gemini con mayor razonamiento
LLM_MODEL="gemini-2.5-flash"

# Opción 3: OpenRouter
LLM_MODEL="meta-llama/llama-3-70b-instruct"
LLM_BASE_URL="https://openrouter.ai/api/v1"
LLM_API_KEY="sk-or-..."
```

---

## 4. Reinicio Limpio (Clean Slate)

Si deseas borrar toda la base de datos histórica, CVs generados y reportes antiguos para empezar de cero:

```bash
# 1. Eliminar base de datos SQLite
rm -f data/db/job_fit.db data/db/job_fit.db-journal

# 2. Eliminar CVs y reportes generados
rm -f data/generated_cvs/*.pdf data/generated_cvs/*.tex
rm -f data/market_study/*.md

# 3. Recrear carpetas vacías
mkdir -p data/db data/generated_cvs data/market_study
```

---

## 5. Automatización con Cron (WSL2 / Linux)

### Ver las tareas programadas activas
```bash
crontab -l
```

### Configuración típica en cron (9:00, 14:00 y 19:00 horas)
```bash
0 9,14,19 * * * cd /home/rigbarra/projects/job-fit && .venv/bin/python -m src.cli scrape >> data/cron.log 2>&1
```

### Si reinicias Windows (Asegurar que el servicio cron esté corriendo en WSL2)
```bash
sudo service cron status
sudo service cron start
```

---

## 6. Verificación de Salud del Proyecto

Ejecutar la suite completa de 39 pruebas unitarias automatizadas:
```bash
.venv/bin/pytest -v
```
