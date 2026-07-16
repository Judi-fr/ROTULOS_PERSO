# Imagen de la API de ROTULOS_PERSO
FROM python:3.13-slim

# No generar .pyc y volcar la salida sin buffer (logs en tiempo real)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias primero para aprovechar la caché de capas:
# si solo cambia el código, no se reinstala nada
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Usuario sin privilegios: el contenedor no corre como root
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Comando por defecto: servidor de producción (gunicorn + settings de prod).
# docker-compose lo sobreescribe en desarrollo con runserver.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
