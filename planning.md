# 🚀 Proyecto: job-fit

Sistema autónomo de ingesta, evaluación de compatibilidad (match score) y adaptación personalizada de CV en formato LaTeX para ofertas laborales técnicas (Analytics / Data Engineer), operando bajo una arquitectura de costo mínimo basada en Python, SQLite y LLMs.

> **Nota de revisión crítica:** Se mantiene la visión original con ajustes concretos de viabilidad en cada sección. Los cambios están marcados con `[REVISADO]` o `[ADVERTENCIA]` donde es necesario.

---

## 1. Visión y Objetivos

* **Filtrado Inteligente:** Eliminar el ruido de alertas de empleo genéricas mediante scraping multi-fuente (Indeed, Remotive, Adzuna — alternativas viables a LinkedIn).
* **Evaluación Automatizada:** Analizar la descripción del puesto frente al perfil base y clasificar por niveles de compatibilidad usando un LLM.
* **Adaptación Dinámica:** Retocar secciones clave del CV en LaTeX (`.tex`) para optimizar palabras clave frente a ATS sin alterar la veracidad de la experiencia.
* **Costo Operativo Mínimo:** `[REVISADO]` La etiqueta "zero-cost" es técnicamente inexacta. El tier gratuito de Gemini tiene límites reales (~250-1500 RPD según el modelo y región). Para uso diario con scraping de ~20-50 vacantes y 2-3 llamadas LLM por vacante, el free tier es suficiente, **pero se debe implementar control de cuota y caché obligatoriamente**.

---

## 2. Flujo del Pipeline (Arquitectura)

```
[ 1. Ingesta / Scraping ] --> [ 2. Deduplicación + Filtro Previo ] --> [ 3. Base de Datos SQLite ]
                                                                                   |
                                                                    [ 4. Agente Evaluador LLM ]
                                                                                   |
                 ┌─────────────────────────────────────────────────────────────────┴──────────────────┐
                 ▼                                                                 ▼                  ▼
          [ Match >= 85% ]                                                [ Match 60%-84% ]   [ Match < 60% ]
        (Postulación Directa)                                             (Requiere Retoque)  (Descarte Silencioso)
                 │                                                                 │                  │
                 │                                                      [ Agente Editor LaTeX ]       ▼
                 │                                                      - Modifica resumen       [ Log en BD ]
                 │                                                      - Ajusta viñetas
                 │                                                      - Compila PDF adaptado
                 │                                                                 │
                 └──────────────────────────┬──────────────────────────────────────┘
                                            ▼
                             [ 5. Notificador (Discord Webhook) ]
                             - Embed con score, sueldo, modalidad, link
                             - PDF adjunto (< 10 MB, límite actual de Discord)
```

`[REVISADO — Paso 2 nuevo]` Se agrega etapa de **deduplicación antes de consultar el LLM**. Es el punto más importante de eficiencia: evita desperdiciar llamadas de API en vacantes ya vistas. El dedup se hace por hash de la URL o del `(empresa + título + fecha)` directamente sobre la base de datos, sin costo de API.

---

## 3. Criterios de Clasificación y Acciones

| Rango de Match | Clasificación | Acción del Agente | Notificación |
| :--- | :--- | :--- | :--- |
| **>= 85%** | **Tier 1: Match Alto** | Compila el CV base sin modificaciones estructurales. | Alerta con prioridad alta, enlace directo y PDF base. |
| **60% - 84%** | **Tier 2: Match con Retoque** | Reescribe el resumen profesional y ajusta viñetas de experiencia con keywords de la vacante; genera un nuevo PDF. | Alerta con resumen de cambios realizados y PDF personalizado adjunto. |
| **< 60%** | **Tier 3: Descarte** | Guarda el registro en la base de datos para métricas. | Silenciado (no genera notificación). |

---

## 4. Stack Tecnológico

### 4.1 Núcleo

* **Entorno:** Debian Linux / WSL2.
* **Lenguaje:** Python 3.11+.
* **Persistencia:** SQLite + SQLAlchemy (o SQLModel).
* **Motor LLM:** Google Gemini API (Free Tier, `gemini-2.5-flash`).
* **Compilación LaTeX:** TeX Live (`pdflatex` / `xelatex`) con plantillas Jinja2.
* **Notificaciones:** Discord Webhooks API.
* **Contenedorización:** Docker + Docker Compose.

### 4.2 Scraping / Ingesta `[REVISADO — CAMBIO CRÍTICO]`

**Problema con LinkedIn:** El plan original usa `python-jobspy` sobre LinkedIn Guest Mode. Esto tiene un riesgo de fallo alto y no mitigable sin costo:

| Riesgo | Impacto | ¿Mitigable sin costo? |
|--------|---------|----------------------|
| LinkedIn actualiza su estructura HTML frecuentemente | El scraper rompe sin aviso | ⚠️ Solo con mantenimiento manual frecuente |
| Rate limiting agresivo (bloqueo por IP) | El pipeline completo se detiene | ❌ Requiere proxies residenciales (tienen costo) |
| Violación de ToS de LinkedIn | Bloqueo de cuenta permanente | ❌ No mitigable |

**Propuesta revisada — Estrategia de fuentes multi-origen:**

```
Fuente Principal (gratis, estable, sin problema de ToS):
  ├── Indeed    --> httpx + BeautifulSoup4 (scraping público tolerado)
  ├── Remotive  --> API REST oficial (100% gratuita, ideal para roles remotos)
  └── Adzuna    --> API REST oficial (tier gratuito: 250 req/día)

Fuente Opcional (bajo volumen, degradación elegante):
  └── LinkedIn  --> python-jobspy con user-agent rotation y throttling agresivo
                   (tratar como fuente "best-effort", nunca como dependencia crítica)
```

**Implementación recomendada:**
- `ScraperBase` → interfaz abstracta con método `fetch_jobs() -> list[JobPost]`
- `IndeedScraper`, `RemotiveScraper`, `AdzunaScraper` → implementaciones concretas
- `LinkedInScraper` → implementación marcada como `unreliable=True`, con circuit breaker
- El orquestador itera fuentes en orden de confiabilidad; LinkedIn es el último.

### 4.3 LLM — Control de Cuota `[REVISADO — RIESGO REAL]`

El free tier de Gemini (`gemini-2.5-flash`) tiene límites de ~250-1500 RPD (varía por cuenta y región). Con 30 vacantes/día y 2 llamadas LLM por vacante (evaluación + reescritura), se consumen **~60 llamadas diarias** — dentro del límite, pero con margen reducido.

**Estrategias obligatorias para no agotar la cuota:**

1. **Caché de evaluaciones:** Hashear el contenido de la descripción del puesto. Si ya fue evaluado, retornar resultado cacheado desde la BD sin nueva llamada a la API.
2. **Prompt combinado:** Evaluar y pedir reescritura en una **sola llamada** cuando `match >= 60%` (en lugar de dos llamadas separadas).
3. **Rate limiter local:** Implementar `asyncio` + semáforo con un máximo de 10 RPM para evitar errores `429`.
4. **Retry con backoff exponencial:** Ante `429 RESOURCE_EXHAUSTED`, reintentar con `2^n * jitter` segundos de espera.

### 4.4 Discord Webhook — Límite de Archivos `[ACLARACIÓN]`

- El límite actual de archivos adjuntos vía webhook es **10 MB** (servidor sin Nitro Boost). Un CV en PDF suele pesar 100 KB – 500 KB. **No es un problema práctico**.
- **Limitación real:** Los webhooks no permiten editar mensajes ya enviados. Si se necesita actualizar el estado de una vacante, se requiere un Bot Token con la Discord Bot API.
- **Recomendación:** Para v1, el webhook es suficiente. Documentar en el README la limitación de edición como deuda técnica para v2.

---

## 5. Estructura de Carpetas del Proyecto

`[REVISADO]` Se alinea la estructura con el stack multi-fuente y se elimina la duplicidad entre `src/` y las carpetas del nivel raíz.

```
job-fit/
├── .github/
│   └── workflows/
│       └── ci.yml                # Lint (ruff), format (black), tests (pytest)
├── config/
│   ├── config.yaml               # Fuentes activas, filtros de búsqueda, umbrales de match
│   └── settings.py               # Validación de env vars con Pydantic Settings
├── data/
│   ├── db/
│   │   └── job_fit.db            # Base de datos SQLite local
│   ├── raw/                      # Payloads crudos (HTML, JSON) para auditoría
│   └── generated_cvs/            # PDFs generados (ej: CV_EmpresaX_20260814.pdf)
├── docker/
│   ├── Dockerfile                # Python 3.11 + TeX Live slim + dependencias
│   └── docker-compose.yml        # Ejecución local y scheduling con cron interno
├── src/
│   ├── __init__.py
│   ├── main.py                   # Entrypoint: orquesta scraping -> eval -> notify
│   │
│   ├── scraper/
│   │   ├── __init__.py
│   │   ├── base.py               # ABC: ScraperBase con método fetch_jobs()
│   │   ├── indeed.py             # Scraper Indeed (httpx + BS4)
│   │   ├── remotive.py           # Cliente API REST Remotive (gratuita)
│   │   ├── adzuna.py             # Cliente API REST Adzuna (free tier)
│   │   └── linkedin.py           # JobSpy wrapper (best-effort, circuit breaker)
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py             # ORM: Job, MatchResult, CVSnapshot
│   │   └── repository.py         # CRUD + deduplicación por hash de URL
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── evaluator.py          # Match % + extracción de keywords faltantes
│   │   ├── rewriter.py           # Generación de texto adaptado para CV
│   │   ├── prompts.py            # System prompts estructurados
│   │   └── quota.py              # Rate limiter local + retry con backoff exponencial
│   │
│   ├── cv_engine/
│   │   ├── __init__.py
│   │   ├── builder.py            # Inyección de texto en plantillas Jinja2
│   │   └── compiler.py           # Ejecución de pdflatex y validación de salida
│   │
│   └── notifier/
│       ├── __init__.py
│       └── discord.py            # Construcción de Embeds + upload de PDF adjunto
│
├── templates/
│   └── cv/
│       ├── cv_base.tex           # Plantilla principal con marcadores Jinja2
│       ├── sections/
│       │   ├── experience.tex
│       │   ├── education.tex
│       │   └── skills.tex
│       └── style.sty             # Tipografía, márgenes y paquetes LaTeX
│
├── tests/
│   ├── conftest.py               # Fixtures compartidas (BD en memoria, mocks de API)
│   ├── test_scraper.py
│   ├── test_evaluator.py
│   ├── test_cv_builder.py
│   └── test_dedup.py             # Tests de deduplicación (nuevo)
│
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

---

## 6. Variables de Entorno Requeridas (`.env.example`)

```dotenv
# LLM Provider
GEMINI_API_KEY=tu_api_key_gratuita_aqui
GEMINI_MODEL=gemini-2.5-flash

# Notificaciones
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/XXXX/YYYY

# Base de Datos
DATABASE_URL=sqlite:///data/db/job_fit.db

# Rutas
OUTPUT_PDF_DIR=./data/generated_cvs
TEMPLATES_DIR=./templates/cv

# Scraping — Fuentes opcionales con free tier
ADZUNA_APP_ID=tu_app_id
ADZUNA_API_KEY=tu_api_key

# Control de cuota LLM (nuevo — crítico para el free tier)
LLM_MAX_CALLS_PER_DAY=150
LLM_MAX_CALLS_PER_MINUTE=10
```

---

## 7. Fases de Desarrollo `[NUEVO]`

Roadmap secuencial que valida cada capa antes de construir la siguiente, minimizando el riesgo de rehacer trabajo.

### Fase 1 — Fundación (Semana 1-2)
**Objetivo:** Pipeline funcional de punta a punta con datos reales, sin LLM todavía.
- [ ] Setup del entorno: Docker, Python, SQLite, Pydantic Settings.
- [ ] Implementar `RemotiveScraper` (fuente más simple: API REST sin auth).
- [ ] Implementar modelos ORM y deduplicación por hash de URL.
- [ ] Implementar `IndeedScraper` con throttling básico.
- [ ] `main.py` que corre scraping → guarda en BD → imprime resultados.
- [ ] Tests unitarios con fixtures y mocks.

### Fase 2 — Inteligencia (Semana 3-4)
**Objetivo:** Integrar el LLM con control de cuota robusto.
- [ ] Implementar `quota.py`: rate limiter local + caché de evaluaciones en BD.
- [ ] Implementar `evaluator.py` con prompt de evaluación y parseo de score.
- [ ] Validar con un batch pequeño (~5 vacantes) los resultados del LLM.
- [ ] Ajustar umbrales de match según resultados reales (60%/85% son hipótesis iniciales).
- [ ] Implementar `AdzunaScraper` y opcionalmente `LinkedInScraper` como best-effort.

### Fase 3 — CV Engine (Semana 5-6)
**Objetivo:** Generación real de PDFs personalizados.
- [ ] Diseñar plantilla LaTeX base y validar compilación manual.
- [ ] Implementar `rewriter.py` (adaptación en una sola llamada junto con la evaluación).
- [ ] Implementar `builder.py` (inyección Jinja2 en template `.tex`).
- [ ] Implementar `compiler.py` (subprocess a `pdflatex`, captura de errores).
- [ ] Tests de integración: vacante de prueba → PDF generado.

### Fase 4 — Notificaciones y Cierre (Semana 7)
**Objetivo:** Sistema completamente autónomo y observable.
- [ ] Implementar `discord.py` con embed formateado y adjunto de PDF.
- [ ] Configurar ejecución diaria (cron en contenedor Docker o GitHub Actions scheduled).
- [ ] Agregar logging estructurado (`loguru` o `structlog`) con rotación de archivos.
- [ ] Documentar README con instrucciones de setup completo.
- [ ] CI/CD: lint + tests automáticos en cada push.

---

## 8. Riesgos y Mitigaciones `[NUEVO]`

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| LinkedIn bloquea el scraper | **Alta** | Alto | Usar fuentes alternativas como principal; LinkedIn como best-effort |
| Agotamiento de cuota del free tier de Gemini | Media | Alto | Caché de evaluaciones + prompt combinado + rate limiter local |
| `pdflatex` falla con caracteres especiales en keywords del LLM | Media | Medio | Sanitizar y escapar caracteres LaTeX en el output del LLM antes de inyectar |
| El LLM alucina datos en el CV adaptado | **Media** | **Muy Alto** | El prompt debe incluir el perfil base completo y prohibir explícitamente inventar experiencia. Validación humana obligatoria para cambios Tier 2. |
| PDF supera 10 MB (límite Discord) | Muy Baja | Bajo | Validar tamaño antes de enviar; comprimir imágenes si las hubiera |

---

## 9. Decisiones de Diseño Pendientes `[NUEVO]`

Ítems que requieren decisión antes o durante el desarrollo:

- **Formato del perfil base:** ¿YAML, JSON o directamente en el `.tex`? → **Recomendación:** YAML separado (`profile.yaml`) que alimenta tanto al LLM (como contexto) como al `builder.py` (como datos para la plantilla). Mantiene la fuente de verdad única.
- **Scheduling:** ¿Cron dentro del contenedor o GitHub Actions gratuito? → GitHub Actions tiene 2000 min/mes gratis; suficiente para ejecución diaria de ~5 min. **Elimina la necesidad de un servidor siempre activo.**
- **Umbrales de match:** ¿Fijos o configurables? → Deben estar en `config.yaml` y ajustarse tras validar con datos reales en la Fase 2. Los valores 60%/85% son hipótesis de partida.
- **Estrategia de notificación:** ¿Por vacante o resumen diario? → **Recomendación:** Tier 1 notificación individual inmediata (urgencia); Tier 2 agrupado en un único embed diario con tabla comparativa.

---

## 1. Visión y Objetivos

* **Filtrado Inteligente:** Eliminar el ruido de alertas de empleo genéricas mediante scraping público (LinkedIn modo incógnito/invitado).
* **Evaluación Automatizada:** Analizar la descripción del puesto frente al perfil base y clasificar por niveles de compatibilidad.
* **Adaptación Dinámica:** Retocar secciones clave del CV en LaTeX (.tex) para optimizar palabras clave frente a ATS sin alterar la veracidad de la experiencia.
* **Cero Costo Operativo:** Utilizar herramientas open source y tiers gratuitos (Google Gemini API / Webhooks de Discord) sin requerir tarjetas de crédito ni suscripciones pagas.

---

## 2. Flujo del Pipeline (Arquitectura)

```
[ 1. Ingesta / Scraping ] ──> [ 2. Base de Datos SQLite ] ──> [ 3. Agente Evaluador LLM ]
                                                                       │
           ┌───────────────────────────────────────────────────────────┴───────────────────────────────┐
           ▼                                                           ▼                               ▼
    [ Match >= 85% ]                                            [ Match 60% - 84% ]             [ Match < 60% ]
  (Postulación Directa)                                         (Requiere Retoque)                (Descarte)
           │                                                           │                               │
           │                                                [ Agente Editor LaTeX ]                    ▼
           │                                                - Modifica resumen y viñetas          [ Log en BD ]
           │                                                - Compila nuevo PDF a medida
           │                                                           │
           └──────────────────────────┬────────────────────────────────┘
                                      ▼
                        [ 4. Notificador (Discord Webhook) ]
                        - Embed con score, sueldo, modalidad, link
                        - Archivo .pdf generado adjunto listo para enviar
```

---

## 3. Criterios de Clasificación y Acciones

| Rango de Match | Clasificación | Acción del Agente | Notificación |
| :--- | :--- | :--- | :--- |
| **>= 85%** | **Tier 1: Match Alto** | Compila el CV base sin modificaciones estructurales. | Alerta con prioridad alta, enlace directo y PDF base. |
| **60% - 84%** | **Tier 2: Match con Retoque** | Reescribe el resumen profesional y ajusta viñetas de experiencia con keywords de la vacante; genera un nuevo PDF. | Alerta con resumen de cambios realizados y PDF personalizado adjunto. |
| **< 60%** | **Tier 3: Descarte** | Guarda el registro en la base de datos para métricas. | Silenciado (no genera notificación diaria). |

---

## 4. Stack Tecnológico

* **Entorno de Desarrollo:** Debian Linux sobre WSL2.
* **Lenguaje:** Python 3.11+.
* **Scraping / Ingesta:** python-jobspy / httpx + BeautifulSoup4 (LinkedIn Guest Mode).
* **Persistencia:** SQLite + SQLAlchemy / SQLModel.
* **Motor LLM:** Google Gemini API (Free Tier).
* **Compilación de Documentos:** TeX Live (pdflatex / xelatex) con plantillas modulares Jinja2.
* **Despacho / Notificaciones:** Discord Webhooks API.
* **Contenedorización:** Docker + Docker Compose.

---

## 5. Estructura de Carpetas del Proyecto

```
job-fit/
├── .github/
│   └── workflows/              # CI/CD (linter, formateo, pruebas automáticas)
│       └── tests.yml
├── config/
│   ├── config.yaml             # Filtros de búsqueda, parámetros de scraping y tramos de match
│   └── settings.py             # Validación de variables de entorno con Pydantic Settings
├── data/
│   ├── db/
│   │   └── job_fit.db          # Base de datos SQLite local
│   ├── raw/                    # Payloads crudos y respuestas HTML para auditoría
│   └── generated_cvs/          # PDFs generados por postulación (ej. CV_EmpresaX.pdf)
├── docker/
│   ├── Dockerfile              # Entorno con Python y paquetes base de TeX Live
│   └── docker-compose.yml      # Definición de ejecución local del contenedor
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entrypoint principal para orquestar la ejecución diaria
│   │
│   ├── scraper/                # Módulos de ingesta y scraping
│   │   ├── __init__.py
│   │   ├── base_scraper.py     # Interfaz base para scrapers
│   │   └── linkedin_guest.py   # Extracción de vacantes vía endpoints públicos de LinkedIn
│   │
│   ├── database/               # Modelos de datos y capa de persistencia
│   │   ├── __init__.py
│   │   ├── models.py           # Modelos de tablas (Jobs, MatchResults, CVSnapshots)
│   │   └── repository.py       # Operaciones CRUD sobre la base de datos
│   │
│   ├── agent/                  # Lógica de agentes LLM
│   │   ├── __init__.py
│   │   ├── evaluator.py        # Cálculo de Match % y extracción de keywords faltantes
│   │   ├── rewriter.py         # Generación de adaptaciones de texto para el CV
│   │   └── prompts.py          # Definición estructurada de system prompts
│   │
│   ├── cv_engine/              # Generación y renderizado de CV en LaTeX
│   │   ├── __init__.py
│   │   ├── builder.py          # Inyección de texto adaptado en plantillas Jinja2
│   │   └── compiler.py         # Ejecución de pdflatex en Linux y validación de salida
│   │
│   └── notifier/               # Capa de comunicación externa
│       ├── __init__.py
│       └── discord.py          # Construcción de Embeds y subida de archivos vía Webhook
│
├── templates/
│   └── cv/                     # Archivos fuente de LaTeX
│       ├── cv_base.tex         # Plantilla principal estructurada con Jinja2
│       ├── sections/           # Subsecciones modulares del CV
│       │   ├── experience.tex
│       │   ├── education.tex
│       │   └── skills.tex
│       └── style.sty           # Configuración tipográfica, márgenes y paquetes LaTeX
│
├── tests/                      # Suite de pruebas unitarias
│   ├── test_scraper.py
│   ├── test_evaluator.py
│   └── test_cv_builder.py
│
├── .env.example                # Variables de entorno requeridas (API Keys, Webhooks)
├── .gitignore                  # Exclusiones de Git (.env, data/, *.pdf, *.aux, *.log)
├── README.md                   # Documentación técnica general
└── requirements.txt            # Dependencias del proyecto
```

---

## 6. Variables de Entorno Requeridas (.env.example)

```
# LLM Provider
GEMINI_API_KEY=tu_api_key_gratuita_aqui
GEMINI_MODEL=gemini-2.5-flash

# Notificaciones
DISCORD_WEBHOOK_URL=[https://discord.com/api/webhooks/XXXX/YYYY](https://discord.com/api/webhooks/XXXX/YYYY)

# Base de Datos
DATABASE_URL=sqlite:///data/db/job_fit.db

# Configuración de Rutas
OUTPUT_PDF_DIR=./data/generated_cvs
TEMPLATES_DIR=./templates/cv
```