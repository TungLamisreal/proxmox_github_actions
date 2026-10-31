FROM python:3.11-slim
LABEL org.opencontainers.image.source="https://github.com/TungLamisreal/proxmox_github_actions"
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
RUN pip install --no-cache-dir flask==3.1.1 gunicorn==23.0.0
COPY app.py /app/app.py
EXPOSE 5000
CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:5000", "app:app"]
