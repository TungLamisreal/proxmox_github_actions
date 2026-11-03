FROM grafana/grafana:13.3.0@sha256:488984b716c0862639f91a698adbe2862677a5a07bad42da5f03f3030883b205
COPY datasource.yaml /etc/grafana/provisioning/datasources/meowcloud.yaml
COPY provider.yaml /etc/grafana/provisioning/dashboards/meowcloud.yaml
COPY autoscale.json /usr/share/grafana/meowcloud/autoscale.json
