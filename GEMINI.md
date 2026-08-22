# Normas de Desarrollo: Filosofía Ponytail (Minimalismo y YAGNI)

Cualquier agente de IA o desarrollador que trabaje en este repositorio debe seguir estrictamente la filosofía **Ponytail** para mantener la base de código compacta, directa y libre de sobreingeniería.

---

## 🪜 La Escalera de Decisiones antes de Programar

Antes de agregar una sola línea de código o una nueva dependencia, hazte las siguientes preguntas en orden:

1. **¿Esto realmente tiene que existir?** (Principio YAGNI: *You Ain't Gonna Need It*). Si la funcionalidad es un "por si acaso", no la escribas.
2. **¿Ya existe en la base de código?** Reutiliza las funciones existentes en `src/database/repository.py` o `src/agent/filter.py`.
3. **¿La biblioteca estándar de Python lo resuelve?** Evita dependencias externas si módulos nativos como `json`, `re`, `datetime` o `urllib` pueden hacer la tarea.
4. **¿Se puede resolver con la dependencia existente?** Ya tenemos `sqlmodel`, `curl_cffi`, `jinja2` y `beautifulsoup4`. No agregues frameworks pesados (como LangChain, LlamaIndex o Celery).
5. **¿Se puede escribir en una sola línea o función simple?** Prefiere la simplicidad legible antes que abstracciones complejas o patrones de diseño innecesarios (como Factory o Repository Patterns redundantes).

---

## 🚫 Restricciones del Proyecto

* **No agregar UI ni Web Servers:** El proyecto es un CLI (Command Line Interface) optimizado para ejecutarse en `cron`. No agregues FastAPI, Flask o interfaces web a menos que sea explícitamente requerido.
* **No agregar frameworks de Agentes de IA:** La orquestación y evaluación LLM se realiza de forma directa en `src/agent/evaluator.py` mediante llamadas directas HTTP. Manténlo así de liviano.
* **No repetir lógica en Scrapers:** Toda lógica común de reintentos, retrasos (throttling), circuit breakers e ingesta de base de datos **debe** residir en `src/scraper/base.py`. Los scrapers individuales solo deben encargarse de la descarga y el parseo de HTML/JSON.
