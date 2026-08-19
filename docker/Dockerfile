FROM python:3.13-slim

# Evitar diálogos interactivos durante la instalación de paquetes
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Instalar herramientas básicas del sistema y TeX Live (edición básica + intermedia)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-lang-spanish \
    latexmk \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Establecer el directorio de trabajo
WORKDIR /app

# Copiar e instalar dependencias de Python
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Crear directorios para base de datos y PDFs si no existen
RUN mkdir -p /app/data/db /app/data/raw /app/data/generated_cvs

# Copiar el resto de la aplicación
COPY . /app/

# El entrypoint ejecutará el orquestador principal
CMD ["python", "src/main.py"]
