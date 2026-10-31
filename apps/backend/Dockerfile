FROM python:3.11-slim
LABEL org.opencontainers.image.source="https://github.com/TungLamisreal/proxmox_github_actions"
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
RUN pip install --no-cache-dir flask==3.1.1 gunicorn==23.0.0 psycopg2-binary==2.9.10 sqlalchemy==2.0.36 alembic==1.14.1
COPY app.py models.py alembic.ini /app/
COPY migrations/ /app/migrations/
EXPOSE 5000
CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:5000", "app:app"]
