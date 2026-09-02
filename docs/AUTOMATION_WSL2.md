# Guía de Automatización 24/7 (WSL2 Debian vs VPS Linux)

Esta guía explica cómo mantener `job-fit` ejecutándose automáticamente 3 veces al día en un entorno Linux / WSL2 Debian.

---

### ❓ La Duda sobre WSL2 Debian (¿Debe la PC estar encendida?)

**Sí.** WSL2 (Windows Subsystem for Linux) es una máquina virtual que se ejecuta **dentro de Windows**. 
- Si tu PC está **apagada o en suspensión (Sleep/Hibernate)**, la instancia de WSL2 también se detiene y las tareas programadas (`cron` de Linux) **no se ejecutarán mientras la PC esté apagada**.
- Las tareas programadas en WSL2 se disparan **únicamente mientras Windows y tu PC permanezcan encendidos**.

---

### 💡 Las 2 Opciones para Automatizar `job-fit`

#### Opción A: Automatización en tu PC (WSL2 Debian / Windows Scheduler)
Si usas tu PC todos los días durante la jornada de trabajo:
1. Abre tu terminal WSL2 y edita el crontab:
   ```bash
   crontab -e
   ```
2. Añade las siguientes líneas para ejecutar la ingesta 3 veces al día (08:30, 13:30, 19:30 hrs):
   ```cron
   30 8,13,19 * * * /home/rigbarra/projects/job-fit/scripts/run_pipeline.sh
   ```
3. Asegúrate de que el servicio `cron` esté corriendo en WSL2:
   ```bash
   sudo service cron status || sudo service cron start
   ```

*(Alternativa Windows sin abrir terminal)*: Puedes crear una **Tarea Programada en Windows (Task Scheduler)** que ejecute al iniciar sesión el comando:
```cmd
wsl.exe -d Debian /home/rigbarra/projects/job-fit/scripts/run_pipeline.sh
```

---

#### Opción B: Ejecución 100% Autónoma 24/7 (VPS Linux en la Nube)
Si no deseas dejar tu PC encendida y quieres que el sistema busque ofertas, evalúe con la IA y te notifique a Discord las 24 horas del día aunque viajes o tengas la PC apagada:

1. **Servidores Recomendados (Gratis o ~$3-$5/mes):**
   - **Oracle Cloud Free Tier** (VPS Linux ARM/x86 100% gratis de por vida).
   - **AWS EC2 Free Tier** (12 meses gratis).
   - **Hetzner / DigitalOcean / Linode** (VPS Debian/Ubuntu por ~$4 USD/mes).

2. **Pasos para Desplegar en VPS:**
   ```bash
   git clone <tu-repo-url> job-fit
   cd job-fit
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env # Configurar tu API key y Discord Webhook
   crontab -e # Añadir la línea de cron
   ```

---

### 🔍 Verificación de Ejecución
Los resultados de cada corrida automática se guardan en:
`logs/cron_pipeline.log`
