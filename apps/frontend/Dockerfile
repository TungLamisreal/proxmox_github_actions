FROM nginx:alpine
LABEL org.opencontainers.image.source="https://github.com/TungLamisreal/proxmox_github_actions"
ARG APP_VERSION=dev
COPY index.html /usr/share/nginx/html/index.html
RUN printf '{"version":"%s","app_edition":"crud-v1"}\n' "$APP_VERSION" > /usr/share/nginx/html/version.json
EXPOSE 80
