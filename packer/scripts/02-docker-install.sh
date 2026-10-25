#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

echo "=== [2/4] Cài đặt Docker Engine và Python SDK ==="
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
apt-get install -y -qq \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin \
    python3-docker

systemctl enable docker
systemctl start docker

echo "=== [PRE-BAKE] Đóng gói sẵn Docker Images (Backend, Frontend, Postgres) vào Golden Template ==="
mkdir -p /opt/meow-backend /opt/meow-frontend/html /opt/meow-frontend/conf

cat << 'EOF' > /opt/meow-backend/Dockerfile
FROM python:3.9-slim
WORKDIR /app
RUN pip install --no-cache-dir flask psycopg2-binary gunicorn
COPY app.py .
CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:5000", "app:app"]
EOF

cat << 'EOF' > /opt/meow-backend/app.py
from flask import Flask, jsonify
import socket

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return jsonify({
        "service": "MeowCloud Backend API (Docker)",
        "node": socket.gethostname(),
        "db_primary": "192.168.150.71",
        "status": "UP"
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
EOF

# Build sẵn meow-backend:latest và pull sẵn nginx:alpine ngay trong lúc tạo Template
docker build -t meow-backend:latest /opt/meow-backend
docker pull nginx:alpine

echo "=== Hoàn tất cài đặt Docker Engine & Pre-baked Images ==="
