FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh && useradd --create-home traficalm
USER traficalm

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
