---
framework_version: 2.0.0
---

# Agent Guidelines: job-fit

Este repositorio es un sistema autónomo e interactivo de ingesta de ofertas laborales, evaluación de fit por IA, compilación de CVs y Cartas de Presentación en LaTeX, y generación de Guías de Entrevistas Técnicas para **Data Engineering y Analytics Engineering en Chile**.

## Single Source of Truth

Toda la configuración del perfil del candidato, parámetros de búsqueda y proveedores de LLM residen en este repositorio:

1. **Perfil del Candidato:** [`config/profile.yaml`](config/profile.yaml)
2. **Configuración del Pipeline:** [`config/config.yaml`](config/config.yaml) y [`.env`](.env)
3. **Plantillas TeX:** [`templates/cv/`](templates/cv/) y [`templates/cover/`](templates/cover/)

## Habilidades y Comandos Disponibles para Antigravity CLI

Puedes invocar las siguientes habilidades directamente usando Antigravity CLI:

- **`/scrape`**: Inicia el pipeline de scraping e ingesta secuencial (Get on Board Chile, LinkedIn Chile, Remotive).
- **`/apply <job_id_or_url>`**: Genera la evaluación detallada de fit, el CV adaptado en LaTeX (.pdf) y la Carta de Presentación (.pdf).
- **`/interview <job_id_or_url>`**: Genera la Guía Completa de Entrevista Técnica y Conductual (.md) específica para el puesto.
- **`/market-study`**: Genera el estudio analítico de mercado con estadísticas salariales, herramientas más cotizadas y recomendaciones de negociación.
