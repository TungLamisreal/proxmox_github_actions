# DB07 build-only compatibility image. CI never deploys this onto live PGDATA.
# Image must be rebuilt/reviewed and pinned by digest before any production handover.
ARG PG_IMAGE=postgres:15-alpine
FROM ${PG_IMAGE}

# Official postgres:15-alpine provides the existing compatible PostgreSQL
# binaries and pg_rewind. Patroni lives in its own Python virtual environment.
# Use Alpine's compiled psycopg2 for the base image's own Python ABI.
# Python 3.14 on rolling Alpine has no wheel for older psycopg2-binary pins.
RUN apk add --no-cache python3 py3-pip py3-psycopg2 \
    && python3 -m venv --system-site-packages /opt/patroni-venv \
    && /opt/patroni-venv/bin/pip install --no-cache-dir "patroni[etcd3]==4.0.6"

ENV PATH="/opt/patroni-venv/bin:${PATH}"
# UID 70 is the existing postgres:15-alpine data owner. Bind-mounted DCS
# credentials and Patroni config must be readable by this user at cutover.
USER postgres
ENTRYPOINT ["/opt/patroni-venv/bin/patroni"]
CMD ["/etc/meowcloud/patroni.yml"]
