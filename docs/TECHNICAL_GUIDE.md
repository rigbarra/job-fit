# 📘 Guía Técnica Paso a Paso de Desarrollo — `job-fit`

Este documento es una **guía técnica exhaustiva** diseñada para estudiar, comprender y explicar en detalle cómo se construyó el proyecto `job-fit` desde cero. Si alguien desea entender el código, las decisiones de diseño y la arquitectura sin necesidad de revisar historiales de conversación, esta guía cubre todo el proceso paso a paso.

---

## 📑 Tabla de Contenidos
1. [Visión General y Filosofía de Diseño (Ponytail)](#1-visión-general-y-filosofía-de-diseño-ponytail)
2. [Recorrido Paso a Paso de la Ejecución (End-to-End Walkthrough)](#2-recorrido-paso-a-paso-de-la-ejecución-end-to-end-walkthrough)
3. [Desglose Técnico Módulo por Módulo](#3-desglose-técnico-módulo-por-módulo)
   - [3.1 Módulo de Configuración (`config/`)](#31-módulo-de-configuración-config)
   - [3.2 Módulo de Base de Datos (`src/database/`)](#32-módulo-de-base-de-datos-srcdatabase)
   - [3.3 Módulo de Scraping y Extracción (`src/scraper/`)](#33-módulo-de-scraping-y-extracción-srcscraper)
   - [3.4 Módulo de Filtrado y Evaluación LLM (`src/agent/`)](#34-módulo-de-filtrado-y-evaluación-llm-srcagent)
   - [3.5 Módulo de Generación LaTeX y PDF (`src/cv_engine/`)](#35-módulo-de-generación-latex-y-pdf-srccv_engine)
   - [3.6 Módulo de Notificaciones Discord (`src/notifier/`)](#36-módulo-de-notificaciones-discord-srcnotifier)
   - [3.7 Orquestador Principal (`src/main.py`)](#37-orquestador-principal-srcmainpy)
4. [Decisiones Críticas de Ingeniería y Resiliencia](#4-decisiones-críticas-de-ingeniería-y-resiliencia)
5. [Guía para Extender o Modificar el Proyecto](#5-guía-para-extender-o-modificar-el-proyecto)

---

## 1. Visión General y Filosofía de Diseño (Ponytail)

El proyecto `job-fit` responde a un problema común: la búsqueda diaria de empleos en portales como LinkedIn e Indeed es repetitiva, genera duplicados y evaluar manualmente la compatibilidad de cada oferta consume mucho tiempo.

Para resolverlo sin incurrir en costos de infraestructura ni en sobreingeniería, el proyecto fue construido bajo la **Filosofía Ponytail (Minimalismo y YAGNI)**:

* **0 Servidores Web / 0 APIs REST expuestas:** Es un CLI puro en Python optimizado para ejecutarse en `cron`.
* **0 Frameworks pesados de Agentes:** No utiliza LangChain, LlamaIndex o Celery. Las llamadas al LLM son peticiones HTTP directas con `curl_cffi` a OpenRouter.
* **0 Bases de Datos Vectoriales:** La persistencia se realiza en SQLite local con deduplicación por hash SHA-256.
* **$0 Costo Operativo:** Usa el tier gratuito de OpenRouter (`openrouter/free` o `google/gemma-3-27b-it:free`) y herramientas nativas de Linux (`pdflatex`, `crontab`).

---

## 2. Recorrido Paso a Paso de la Ejecución (End-to-End Walkthrough)

Cuando el sistema se ejecuta (a las 9:00 AM vía `crontab` o mediante `PYTHONPATH=. .venv/bin/python src/main.py`), sigue esta secuencia exacta:

```
[1. Config & BD] ──> [2. Grupos de Scraping] ──> [3. Pre-Filtrado Local] ──> [4. Evaluación LLM] ──> [5. Generación PDF] ──> [6. Notificación]
```

1. **Inicialización (`init_db` & `load_config`):** Se verifica la presencia del esquema SQLite en `data/db/job_fit.db` y se carga `config.yaml` en memoria.
2. **Scraping por Grupos Secuenciales de Prioridad:**
   - **Grupo 1 (Chile):** Indeed Chile $\rightarrow$ LinkedIn Chile.
   - **Grupo 2 (Internacional):** Remotive $\rightarrow$ Indeed Internacional $\rightarrow$ LinkedIn Internacional.
3. **Deduplicación Previa:** Antes de descargar el cuerpo HTML o detalle de cada puesto, se calcula `hash_url = SHA-256(url)`. Si la URL ya está registrada en la BD, la petición de red se omite inmediatamente (ahorro de ancho de banda e IP).
4. **Pre-Filtrado Algorítmico Local (`should_evaluate_job`):** Evalúa título, palabras clave obligatorias (`sql`), ventana de publicación (24h) y modalidad (híbrido presencial en Chile vs 100% remoto internacional). Si falla, se marca como Tier 3 directamente en la BD sin gastar tokens de LLM.
5. **Evaluación ATS con LLM (`evaluate_job`):** Las vacantes aprobadas se envían a OpenRouter. El LLM retorna `score` (0-100), `rationale`, `missing_keywords`, y si el score está entre 60 y 84 (Tier 2), retorna `adapted_summary` y `adapted_bullets`.
6. **Compilación de CV (`generate_cv_for_job`):** Si la oferta califica según las `notification_rules` (Tier 1 o Tier 2), Jinja2 renderiza la plantilla LaTeX (`cv_base_es.tex` o `cv_base_en.tex`) aplicando escapado de caracteres TeX, y `pdflatex` genera el PDF en un entorno temporal aislado.
7. **Notificación en Discord (`send_job_notification`):** Se envía un Embed formateado a Discord con etiqueta `[CHILE]` o `[INTL]`, indicador de color por Tier y el archivo PDF adjunto vía `multipart/form-data`.

---

## 3. Desglose Técnico Módulo por Módulo

### 3.1 Módulo de Configuración (`config/`)

* **`settings.py`:** Utiliza `pydantic-settings` para cargar variables de entorno desde `.env` (`OPENROUTER_API_KEY`, `DISCORD_WEBHOOK_URL`, `DATABASE_URL`).
* **`loader.py`:** Implementa `load_config()` que lee `config/config.yaml` y cachea el diccionario en memoria en la variable global `_cached_config` para evitar accesos I/O repetidos a disco.
* **`profile.yaml`:** Almacena la hoja de vida estructurada del candidato (experiencia, tecnologías, educación y proyectos) tanto en español como en inglés.

### 3.2 Módulo de Base de Datos (`src/database/`)

El modelo utiliza **SQLModel** (híbrido entre SQLAlchemy 2.0 y Pydantic):

* **`models.py`:**
  * `Job`: Campos `id`, `title`, `company`, `location`, `description`, `url`, `hash_url` (único e indexado), `source`, `salary`, `posted_at`, `created_at`.
  * `MatchResult`: Guarda `job_id`, `score`, `tier` (1, 2 o 3), `rationale`, `missing_keywords`, `adapted_summary`, `adapted_bullets`.
  * `CVSnapshot`: Registra `job_id`, `pdf_path`, `tex_path` y la fecha de compilación.
* **`repository.py`:** Contiene las operaciones CRUD del sistema (`init_db`, `save_job`, `get_pending_jobs`, `save_match_result`, `get_match_results_count_today`).

### 3.3 Módulo de Scraping y Extracción (`src/scraper/`)

* **`base.py` (`WebScraper`):** Clase base abstracta que implementa el *Template Method Pattern*:
  * **Throttling:** Pausas aleatorias con `random.uniform(4.0, 8.0)` segundos entre peticiones.
  * **TLS Impersonation:** Uso de `curl_cffi.requests` con `impersonate="chrome120"` para imitar el handshake TLS de navegadores reales.
  * **Circuit Breaker:** Detiene el barrido si recibe respuestas `403 Forbidden` o `429 Too Many Requests`.
* **`linkedin.py` (`LinkedInScraper`):** Consulta la Guest API de LinkedIn (`jobs-guest/jobs/api/seeMoreJobPostings/search`). Construye parámetros URL nativos como `f_TPR` (antigüedad), `f_WT` (modalidad) y `f_E` (seniority).
* **`remotive.py` (`RemotiveScraper`):** Consulta la API REST pública de Remotive (`https://remotive.com/api/remote-jobs`).
* **`indeed.py` (`IndeedScraper`):** Scraper HTML/JSON que extrae las tarjetas de empleo parseando la estructura interna de JavaScript `window.mosaic.providerData`.

### 3.4 Módulo de Filtrado y Evaluación LLM (`src/agent/`)

* **`filter.py`:** `should_evaluate_job(job)` realiza la validación algorítmica local (0 tokens). Exporta la constante `CHILE_TERMS` compartida por todo el proyecto para identificar geográficamente empleos de Chile.
* **`evaluator.py`:**
  * Detecta el idioma de la oferta (Español o Inglés).
  * Carga el perfil relevante (`profile.yaml`).
  * `normalize_llm_json(raw_json)`: Función de limpieza que elimina bloques `<think>...</think>` (característicos de modelos de razonamiento como DeepSeek), extrae bloques ````json ... ```` y mapea cualquier clave variante (`match_score`, `missing_skills`) a la estructura Pydantic `MatchEvaluation`.
* **`quota.py`:**
  * `check_daily_quota()`: Consulta la BD para asegurar que no se supere `LLM_MAX_CALLS_PER_DAY`.
  * `enforce_rpm()`: Registra marcas de tiempo en los últimos 60 segundos para evitar sobrepasar `LLM_MAX_CALLS_PER_MINUTE`.
  * `call_with_retry()`: Envía la petición con retardo exponencial (*backoff*) y ruido aleatorio (*jitter*).

### 3.5 Módulo de Generación LaTeX y PDF (`src/cv_engine/`)

* **`builder.py`:**
  * `escape_latex(text)`: Escapa caracteres tipográficos conflictivos en TeX (`%`, `&`, `$`, `#`, `_`, `{`, `}`, `^`, `~`, `"`).
  * `build_cv_tex()`: Renderiza las plantillas Jinja2 (`templates/cv/cv_base_es.tex` o `cv_base_en.tex`) inyectando las viñetas y el resumen adaptados si la vacante es Tier 2.
* **`compiler.py`:**
  * `detect_job_language(job)`: Analiza la descripción del empleo mediante conteo de palabras clave para determinar si la vacante está en español o inglés.
  * `compile_tex_to_pdf(tex_content, output_pdf_path)`: Escribe el archivo `.tex` en un directorio temporal (`tempfile`) y ejecuta `pdflatex` mediante `subprocess.run()`.

### 3.6 Módulo de Notificaciones Discord (`src/notifier/`)

* **`discord.py` (`send_job_notification`):**
  * Asigna prefijo visual `[CHILE]` o `[INTL]`.
  * Asigna color al Embed: 🟩 Verde (`0x2ECC71`) para Tier 1 o 🟨 Dorado (`0xF1C40F`) para Tier 2.
  * Construye una petición HTTP `POST` multipart (`multipart/form-data`) usando `urllib.request` nativo de Python con boundary `uuid.uuid4().hex` para adjuntar el PDF compilado directamente a la notificación de Discord.

### 3.7 Orquestador Principal (`src/main.py`)

Une todos los componentes en un flujo secuencial robusto:
1. `init_db()`
2. Lectura de `config.yaml` y reglas `notification_rules`.
3. Iteración sobre `execution_groups`.
4. Extracción $\rightarrow$ Deduplicación $\rightarrow$ Filtrado $\rightarrow$ Evaluación LLM $\rightarrow$ Generación PDF $\rightarrow$ Notificación Discord.
5. Captura de excepciones `DailyQuotaExhaustedError` y `RateLimitError` para pausar la corrida sin perder datos.

---

## 4. Decisiones Críticas de Ingeniería y Resiliencia

1. **Defensa ante Cambios de Esquema en Modelos LLM Gratuitos:**
   Los modelos gratuitos en OpenRouter sufren variaciones de formato con frecuencia. La función `normalize_llm_json()` en `evaluator.py` actúa como una capa defensiva que garantiza que la salida siempre se convierta correctamente al modelo estricto de Pydantic.
2. **Priorización de Grupos para Protección de Cuota:**
   El orquestador procesa primero las búsquedas locales en Chile antes de consumir cuota en búsquedas internacionales. Si la cuota de 150 llamadas/día se agota a mitad del barrido, los empleos de mayor prioridad para el usuario ya fueron procesados.
3. **Escapado Dinámico TeX:**
   LaTeX es extremadamente estricto. La función `escape_latex()` en `builder.py` previene que viñetas reescritas por el LLM rompan la compilación tipográfica con el comando `pdflatex`.

---

## 5. Guía para Extender o Modificar el Proyecto

### Agregar un Nuevo Scraper (Ejemplo: `GetonboardScraper`)
1. Crear `src/scraper/getonboard.py` heredando de `WebScraper`.
2. Implementar los 3 métodos obligatorios:
   * `_build_search_url(keyword, location)`
   * `_parse_search_results(response, location)`
   * `_extract_description(url, response)`
3. Registrar la nueva fuente en `config/config.yaml` bajo `sources: getonboard: true`.
4. Agregar el scraper al array `execution_groups` en `src/main.py`.

### Ejecutar y Extender las Pruebas Unitarias
Para correr la suite de 24 tests unitarios:
```bash
.venv/bin/python -m pytest -v
```
Todos los tests utilizan bases de datos SQLite en memoria (`sqlite:///:memory:`) y `unittest.mock` para asegurar ejecuciones instantáneas y deterministas sin consumo de red.
