#!/usr/bin/env bash
set -Eeuo pipefail
# Keep the same application and image tags currently referenced by Ansible.
mkdir -p /opt/meow-backend /opt/meow-frontend/html /opt/meow-frontend/conf
cat > /opt/meow-backend/Dockerfile <<'EOF'
FROM python:3.9-slim
WORKDIR /app
RUN pip install --no-cache-dir flask psycopg2-binary gunicorn
COPY app.py .
CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:5000", "app:app"]
EOF
cat > /opt/meow-backend/app.py <<'EOF'
from flask import Flask, jsonify
import socket
app = Flask(__name__)
@app.route('/')
@app.route('/health')
def health():
    return jsonify({"service":"MeowCloud Backend API (Docker)","node":socket.gethostname(),"db_primary":"192.168.150.71","status":"UP"})
if __name__ == '__main__':
    app.run(host='0.0.0.0',port=5000)
EOF
docker build -t meow-backend:latest /opt/meow-backend
docker pull nginx:alpine
docker image inspect meow-backend:latest nginx:alpine >/dev/null
