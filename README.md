# 🚀 job-fit

Sistema autónomo de ingesta, evaluación de compatibilidad (match score) y adaptación personalizada de CV en formato LaTeX para ofertas laborales técnicas (Analytics / Data Engineer), operando bajo una arquitectura de costo mínimo basada en Python, SQLite y LLMs gratuitos (OpenRouter).

Actualmente, el proyecto se encuentra al finalizar la **Fase 2: Inteligencia**, con pre-filtrado algorítmico local implementado.

---

## 📋 Características Implementadas

### Fase 1 — Fundación ✅
- **Ingesta Multi-fuente:** Scrapers implementados para Remotive (API REST pública) e Indeed (scraping con `curl_cffi` para evadir bloqueos básicos).
- **Deduplicación Previa:** Hashing SHA-256 de las URLs de vacantes para evitar procesamiento y llamadas de API repetidas.
- **Persistencia Local:** Base de datos relacional ultraliviana usando SQLite y ORM mediante `SQLModel`.
- **Estructura Modular:** Clean architecture (`src/scraper`, `src/database`, `src/agent`, etc.).
- **Suite de Pruebas:** Pruebas unitarias con base de datos en memoria (`sqlite://`).

### Fase 2 — Inteligencia ✅
- **Pre-Filtrado Algorítmico Local:** Descarte automático de vacantes irrelevantes por palabras clave en título y descripción, sin consumir tokens de IA.
- **Evaluación LLM vía OpenRouter:** Análisis de compatibilidad usando modelos gratuitos con control de cuotas (RPM + límite diario + backoff exponencial ante 429).
- **Perfil Real del Candidato:** CVs integrados en español e inglés (LaTeX + Markdown) con contexto de empresas e industrias.
- **Clasificación por Tiers:** Score automático con umbrales configurables (Tier 1 ≥85%, Tier 2 ≥60%, Tier 3 <60%).

### Pendientes
- **Fase 3 — CV Engine:** Generación dinámica de PDFs personalizados con Jinja2 + LaTeX.
- **Fase 4 — Notificaciones:** Despacho de alertas y PDFs adjuntos vía Discord Webhooks.

---

## 🛠️ Requisitos e Instalación

### Requisitos Previos
- **Python 3.11+**
- **Docker & Docker Compose** (opcional para ejecución aislada)
- **TeX Live** (necesario para compilar el CV LaTeX — Fase 3)

### Instalación Local
1. Clonar el repositorio:
   ```bash
   git clone <repo-url> job-fit
   cd job-fit
   ```

2. Crear e iniciar el entorno virtual:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Instalar dependencias:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. Configurar variables de entorno:
   ```bash
   cp .env.example .env
   ```
   Edita `.env` e ingresa tu `OPENROUTER_API_KEY` (obtenida en [openrouter.ai](https://openrouter.ai)). El modelo por defecto (`openrouter/free`) enruta automáticamente a modelos gratuitos activos.

---

## 🚀 Ejecución del Pipeline

```bash
PYTHONPATH=. python src/main.py
```

El pipeline ejecuta en orden:
1. **Scraping** → Busca vacantes en las fuentes activas (`config/config.yaml`).
2. **Deduplicación** → Descarta vacantes ya vistas por hash de URL.
3. **Pre-Filtrado Algorítmico** → Descarta localmente vacantes sin keywords de datos/SQL.
4. **Evaluación LLM** → Analiza las vacantes restantes contra tu perfil real (solo si hay API key configurada).
5. **Métricas** → Imprime resumen de la ejecución.

---

## 🧪 Pruebas Unitarias

```bash
PYTHONPATH=. pytest
```

Actualmente 9 tests pasando (scrapers, deduplicación, evaluador LLM, filtro algorítmico). Todas las pruebas corren 100% offline con BD en memoria.

---

## 📂 Estructura del Directorio
```
job-fit/
├── config/              # Configuración: filtros (config.yaml), perfil (profile.yaml), CVs base (md)
├── data/                # Almacenamiento local (SQLite, PDFs generados)
├── docker/              # Dockerfile (con TeX Live) y docker-compose.yml
├── src/                 # Código fuente
│   ├── agent/           # Pre-filtrado algorítmico, evaluador LLM y gestión de cuotas
│   ├── cv_engine/       # Renderizado e inyección Jinja2 en LaTeX (Fase 3)
│   ├── database/        # Modelos ORM y repositorios
│   ├── notifier/        # Despacho Discord (Fase 4)
│   └── scraper/         # Scrapers (Remotive, Indeed, etc.)
├── templates/           # Plantillas LaTeX (.tex) del CV en español e inglés
├── tests/               # Suite de pruebas unitarias con mocks
└── requirements.txt     # Dependencias Python
```

---

## ⚙️ Configuración

### Variables de Entorno (`.env`)
| Variable | Descripción | Requerida |
|---|---|---|
| `OPENROUTER_API_KEY` | API key de OpenRouter | Sí (para evaluación LLM) |
| `OPENROUTER_MODEL` | Modelo a usar (default: `openrouter/free`) | No |
| `DISCORD_WEBHOOK_URL` | Webhook de Discord para notificaciones | No (Fase 4) |
| `LLM_MAX_CALLS_PER_DAY` | Límite diario de llamadas LLM (default: 150) | No |

### Filtros de Búsqueda (`config/config.yaml`)
- **Keywords:** Palabras clave de búsqueda (ej: "Analytics Engineer", "Data Engineer").
- **Locations:** Ubicaciones a filtrar (ej: "Remote", "Chile").
- **Filtro Algorítmico:** Keywords obligatorias en título y descripción para pre-filtrado local.
