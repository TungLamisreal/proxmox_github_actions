FROM nginx:alpine
ARG APP_VERSION=dev
COPY index.html /usr/share/nginx/html/index.html
RUN printf '{"version":"%s"}\n' "$APP_VERSION" > /usr/share/nginx/html/version.json
EXPOSE 80
