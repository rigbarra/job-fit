# 🚀 job-fit

Sistema autónomo de ingesta, evaluación de compatibilidad (match score) y adaptación personalizada de CV en formato LaTeX para ofertas laborales técnicas (Analytics / Data Engineer), operando bajo una arquitectura de costo mínimo basada en Python, SQLite y LLMs.

Actualmente, el proyecto se encuentra al finalizar la **Fase 1: Fundación**.

---

## 📋 Características de la Fase 1
- **Ingesta Multi-fuente:** Scrapers implementados para Remotive (API REST pública y estable) e Indeed (scraping emulando navegador con `curl_cffi` para evadir bloqueos básicos).
- **Deduplicación Previa:** Hashing SHA-256 de las URLs de vacantes para evitar procesamiento y llamadas de API repetidas.
- **Persistencia Local:** Base de datos relacional ultraliviana usando SQLite y ORM mediante `SQLModel`.
- **Estructura Modular:** Modularización del código siguiendo principios de clean architecture (`src/scraper`, `src/database`, `src/agent`, etc.).
- **Suite de Pruebas:** Pruebas unitarias de scrapers y deduplicación con base de datos en memoria (`sqlite://`).

---

## 🛠️ Requisitos e Instalación

### Requisitos Previos
- **Python 3.11+**
- **Docker & Docker Compose** (opcional para ejecución aislada)
- **TeX Live** (necesario en la máquina local o contenedor para compilar el CV LaTeX)

### Instalación Local
1. Clonar el repositorio y acceder a él:
   ```bash
   git clone <repo-url> job-fit
   cd job-fit
   ```

2. Crear e iniciar el entorno virtual:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # En Linux/macOS
   ```

3. Instalar las dependencias de Python:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. Configurar las variables de entorno:
   ```bash
   cp .env.example .env
   ```
   *Edita el archivo `.env` para ingresar tu `GEMINI_API_KEY` y configurar el webhook de Discord.*

---

## 🚀 Ejecución del Pipeline

Para correr la ingesta de empleos localmente, ejecuta:
```bash
PYTHONPATH=. python src/main.py
```

Esto buscará ofertas para las palabras clave definidas en [`config/config.yaml`](file:///home/rigbarra/projects/job-fit/config/config.yaml), aplicará los filtros de ubicación, las deduplicará e ingresará las nuevas ofertas en la base de datos local SQLite (`data/db/job_fit.db`).

---

## 🧪 Pruebas Unitarias
El proyecto cuenta con pruebas automatizadas usando `pytest`. Para ejecutarlas:
```bash
PYTHONPATH=. pytest
```

---

## 📂 Estructura del Directorio
```
job-fit/
├── config/              # Filtros de búsqueda (config.yaml) y configuración del entorno (settings.py)
├── data/                # Almacenamiento local (SQLite, PDFs generados y payloads crudos)
├── docker/              # Dockerfile (con TeX Live) y docker-compose.yml
├── src/                 # Código fuente
│   ├── agent/           # Evaluador de match y reescritura de currículum
│   ├── cv_engine/       # Renderizado e inyección Jinja2 en LaTeX
│   ├── database/        # Conexión, modelos de base de datos y repositorios
│   ├── notifier/        # Despacho de notificaciones y adjuntos de PDF a Discord
│   └── scraper/         # Módulos concretos de scraping (Remotive, Indeed, etc.)
├── templates/           # Plantillas LaTeX modulares (.tex y .sty)
├── tests/               # Pruebas automatizadas y mocks
└── requirements.txt     # Dependencias de Python
```
