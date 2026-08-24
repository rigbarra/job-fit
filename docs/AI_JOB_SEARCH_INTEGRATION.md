# 🚀 Integración y Mejoras Inspiradas en `ai-job-search`

Documentación de los cambios implementados en la rama **`feature/ai-job-search-enhancements`** para potenciar el proyecto **`job-fit`** combinando la automatización de ingesta en Chile con la metodología de agentes de **`ai-job-search`**.

---

## 1. 🇨🇱 Ingesta de Datos: Nuevo Scraper Get on Board Chile

Se implementó el módulo `GetOnBoardScraper` ([src/scraper/getonboard.py](file:///home/rigbarra/projects/job-fit/src/scraper/getonboard.py)) para consultar de forma oficial la API REST v0 de **Get on Board** (`getonbrd.com`), el portal líder en Chile para ofertas de **Data Engineering, Analytics Engineering y Data Science**.

### Ventajas:
- **API REST Pública Oficial:** No depende de scraping HTML frágil ni de proxies de navegador.
- **Resolución de Empresas y Salarios:** Obtiene nombres exactos de empresas con caché en memoria y muestra rangos salariales explícitos en USD / CLP.
- **Integrado en el Flujo Secuencial:** Priorizado en la ejecución local de Chile (`main.py`) antes de gastar cuotas o llamados a red.

---

## 2. 🔀 Arquitectura Multi-Proveedor de LLM (Antigravity, Gemini & OpenRouter)

Para no depender de los modelos gratuitos limitados de OpenRouter, se diseñó la capa de abstracción `BaseLLMProvider` en [src/agent/providers.py](file:///home/rigbarra/projects/job-fit/src/agent/providers.py).

### Proveedores Soportados:
1. **`gemini` (Google Gemini API / Antigravity):** Conecta directamente con la API oficial de Google Gemini (`gemini-2.0-flash`, `gemini-2.5-flash`). Permite usar tu cuenta PRO o API Key oficial.
2. **`openrouter` (OpenRouter Paid/Free):** Mantiene soporte para modelos de OpenRouter (`google/gemma-3-27b-it:free`, `anthropic/claude-3.5-sonnet`, `openai/gpt-4o-mini`).
3. **`openai` (OpenAI / vLLM / Local):** Soporta endpoints de OpenAI o servidores locales/remotos (Ollama / vLLM / LiteLLM).

### Cómo Cambiar de Proveedor:
En tu archivo `.env`:
```env
# Opción 1: Usar Google Gemini API (Recomendado para Antigravity Pro)
LLM_PROVIDER=gemini
GEMINI_API_KEY=tu_gemini_api_key_aqui
GEMINI_MODEL=gemini-2.0-flash

# Opción 2: Usar OpenRouter (Gratis o Pago)
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=tu_openrouter_api_key
OPENROUTER_MODEL=google/gemma-3-27b-it:free
```

---

## 3. 🎯 Evaluación Multidimensional (Inspirada en `04-job-evaluation.md`)

Se enriqueció el motor de evaluación ([src/agent/prompts.py](file:///home/rigbarra/projects/job-fit/src/agent/prompts.py) y [src/agent/evaluator.py](file:///home/rigbarra/projects/job-fit/src/agent/evaluator.py)) incorporando el esquema de 5 dimensiones de `ai-job-search`:

1. **Compuertas de Elegibilidad e Idioma (Hard Gates):** Mismatches de visa o idioma asignan descarte directo (< 60%).
2. **Technical Skills Match (30%):** Coincidencia en stack principal (SQL, Python, PySpark, dbt, Cloud AWS/GCP/Azure, Airflow, Kimball Data Modeling).
3. **Experience & Seniority Match (25%):** Evaluación funcional de tareas de Data Engineering (Mid a Senior).
4. **Behavioral & Culture Fit (15%):** Balance entre construcción de pipelines vs soporte/mantenimiento pasivo.
5. **Career Alignment & Growth (30%):** Proyección en la ruta de carrera de datos.

---

## 4. 🤝 Flujo Híbrido Recomendado

```
[Invocación Diaria job-fit (Cron / Main)]
  ├── Ingesta Get on Board Chile, LinkedIn Chile, Remotive
  ├── Pre-filtro Local 0 Tokens (SQL, palabras clave, presencialidad)
  ├── Evaluación LLM (Gemini / Antigravity / OpenRouter)
  └── Notificación a Discord con PDF generado
            │
            ▼ (Para vacantes destacadas Tier 1 / Tier 2)
[Antigravity CLI con ai-job-search]
  ├── /apply <url_oferta>
  ├── Generación de Carta de Presentación personalizada (cover_letters)
  └── /interview (Preparación de preguntas técnicas de entrevista)
```
