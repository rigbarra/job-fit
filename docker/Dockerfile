FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Instalar herramientas esenciales y TeX Live para compilación de CVs en LaTeX
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-lang-spanish \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Crear estructura de carpetas de datos y salidas
RUN mkdir -p /app/data/db /app/data/raw /app/data/generated_cvs /app/output

COPY . /app/

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["run"]
