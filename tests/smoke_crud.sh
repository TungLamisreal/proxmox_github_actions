#!/usr/bin/env bash
# Disposable integration tests on ubuntu-latest only. Never touch production KVM/LXC.
set -Eeuo pipefail
BE_IMAGE="${1:?backend image digest}"
GIT_SHA="${2:?commit SHA}"
NETWORK="meow-crud-ci"
TOKEN="ci-ephemeral-token-123"
PW="ci-only-postgres-password"

cleanup() {
  docker rm -f meow-be-crud-ci meow-pg-crud-ci >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$NETWORK" >/dev/null
docker run -d --rm --network "$NETWORK" --name meow-pg-crud-ci \
  -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=meowcloud_demo postgres:15-alpine >/dev/null

# Use a non-superuser database OWNER just like the live meowcrud role.
# DB initdb has a temporary server; retry these idempotent operations until stable.
ROLE_READY=false
for i in $(seq 1 45); do
  if docker exec -i meow-pg-crud-ci psql -X -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null 2>&1 <<'SQL'
DO $meow$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'meowcrud') THEN
    CREATE ROLE meowcrud LOGIN PASSWORD 'ci-only-postgres-password';
  END IF;
END $meow$;
ALTER DATABASE meowcloud_demo OWNER TO meowcrud;
SQL
  then ROLE_READY=true; break; fi
  sleep 2
done
if [[ "$ROLE_READY" != "true" ]]; then
  docker logs --tail 30 meow-pg-crud-ci
  echo "::error::Cannot initialize isolated CI DB owner role"
  exit 1
fi

# PostgreSQL initdb briefly starts and stops a temporary server. Wait for an
# actual connection before applying the versioned Alembic migration.
SCHEMA_READY=false
for i in $(seq 1 45); do
  if docker exec meow-pg-crud-ci psql -X -v ON_ERROR_STOP=1 -U postgres -d meowcloud_demo -c 'SELECT 1;' >/dev/null 2>&1; then
    # Alembic schema upgrades are transactional on PostgreSQL and can be retried.
    if docker run --rm --network "$NETWORK" \
       -e DB_HOST=meow-pg-crud-ci -e DB_PORT=5432 -e DB_NAME=meowcloud_demo \
       -e DB_USER=meowcrud -e DB_PASSWORD="$PW" \
       "$BE_IMAGE" alembic -c /app/alembic.ini upgrade head >/dev/null 2>&1; then
       SCHEMA_READY=true
       break
    fi
  fi
  sleep 2
done
if [[ "$SCHEMA_READY" != "true" ]]; then
  docker logs --tail 30 meow-pg-crud-ci
  echo "::error::Alembic schema migration failed"
  exit 1
fi
# Idempotence: repeating the same migration must leave all data intact.
docker run --rm --network "$NETWORK" \
  -e DB_HOST=meow-pg-crud-ci -e DB_PORT=5432 -e DB_NAME=meowcloud_demo \
  -e DB_USER=meowcrud -e DB_PASSWORD="$PW" \
  "$BE_IMAGE" alembic -c /app/alembic.ini upgrade head
docker exec meow-pg-crud-ci psql -X -U postgres -d meowcloud_demo -At \
  -c 'SELECT version_num FROM alembic_version' | grep -qx '0001_initial_crud'
# Same CLI that Ansible migration job uses to verify the current head.
docker run --rm --network "$NETWORK" \
  -e DB_HOST=meow-pg-crud-ci -e DB_PORT=5432 -e DB_NAME=meowcloud_demo \
  -e DB_USER=meowcrud -e DB_PASSWORD="$PW" \
  "$BE_IMAGE" alembic -c /app/alembic.ini current | grep -F '(head)'


docker run -d --rm --network "$NETWORK" --name meow-be-crud-ci \
  -p 127.0.0.1:18095:5000 \
  -e DB_HOST=meow-pg-crud-ci -e DB_PORT=5432 \
  -e DB_NAME=meowcloud_demo -e DB_USER=meowcrud -e DB_PASSWORD="$PW" \
  -e DEMO_TOKEN="$TOKEN" "$BE_IMAGE" >/dev/null

API=http://127.0.0.1:18095
for i in $(seq 1 45); do
  curl -fsS "$API/db-health" >/dev/null 2>&1 && break
  sleep 1
done

curl -fsS "$API/health" | jq -e '.status == "UP"'
curl -fsS "$API/version" | jq -e --arg sha "$GIT_SHA" '.version == $sha and .app_edition == "crud-v2"'
curl -fsS "$API/db-health" | jq -e '.status == "UP"'

unauthorized="$(curl -s -o /dev/null -w '%{http_code}' "$API/products")"
[[ "$unauthorized" == "401" ]] || { echo "::error::CRUD must require demo token"; exit 1; }

AUTH=(-H "X-Demo-Token: $TOKEN")
JSON=(-H "Content-Type: application/json")

category_id="$(curl -fsS "${AUTH[@]}" "$API/categories" | jq -er '.[0].id')"

product_body="$(jq -nc --argjson id "$category_id" '{category_id:$id,sku:"CI-SKU-001",name:"Sản phẩm CI",price:"125000.00",stock:5}')"
product_id="$(curl -fsS "${AUTH[@]}" "${JSON[@]}" -X POST -d "$product_body" "$API/products" | jq -er '.id')"

customer_body='{"name":"Khách hàng CI","email":"ci@example.test","phone":"0123456789"}'
customer_id="$(curl -fsS "${AUTH[@]}" "${JSON[@]}" -X POST -d "$customer_body" "$API/customers" | jq -er '.id')"

order_body="$(jq -nc --argjson c "$customer_id" --argjson p "$product_id" '{customer_id:$c,product_id:$p,quantity:2,status:"pending"}')"
order_id="$(curl -fsS "${AUTH[@]}" "${JSON[@]}" -X POST -d "$order_body" "$API/orders" | jq -er '.id')"

curl -fsS "${AUTH[@]}" "$API/stats" | jq -e '.products == 1 and .customers == 1 and .orders == 1'

product_update="$(jq -nc --argjson id "$category_id" '{category_id:$id,sku:"CI-SKU-001",name:"Sản phẩm CI cập nhật",price:"155000.00",stock:3}')"
curl -fsS "${AUTH[@]}" "${JSON[@]}" -X PUT -d "$product_update" "$API/products/$product_id" |
  jq -e '.stock == 3 and .name == "Sản phẩm CI cập nhật"'

# v2 read-only inventory analysis uses the same v1 tables and preserves all rows.
report_unauthorized="$(curl -s -o /dev/null -w '%{http_code}' "$API/reports/low-stock?threshold=5")"
[[ "$report_unauthorized" == "401" ]] || { echo "::error::Report must require demo token"; exit 1; }
report_invalid="$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$API/reports/low-stock?threshold=bad")"
[[ "$report_invalid" == "400" ]] || { echo "::error::Invalid report threshold must return 400"; exit 1; }
curl -fsS "${AUTH[@]}" "$API/reports/low-stock?threshold=5" |
  jq -e --argjson product "$product_id" '.threshold == 5 and .total == 1 and (.items | length) == 1 and .items[0].id == $product and .items[0].stock == 3'
curl -fsS "${AUTH[@]}" "$API/reports/low-stock?threshold=2" |
  jq -e '.threshold == 2 and .total == 0 and (.items | length) == 0'

order_update="$(jq -nc --argjson c "$customer_id" --argjson p "$product_id" '{customer_id:$c,product_id:$p,quantity:2,status:"paid"}')"
curl -fsS "${AUTH[@]}" "${JSON[@]}" -X PUT -d "$order_update" "$API/orders/$order_id" |
  jq -e '.status == "paid"'

# Foreign key must prevent accidental product deletion while a related order exists.
conflict="$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" -X DELETE "$API/products/$product_id")"
[[ "$conflict" == "409" ]] || { echo "::error::FK delete protection is missing"; exit 1; }

curl -fsS "${AUTH[@]}" -X DELETE "$API/orders/$order_id"
curl -fsS "${AUTH[@]}" -X DELETE "$API/products/$product_id"
curl -fsS "${AUTH[@]}" -X DELETE "$API/customers/$customer_id"
curl -fsS "${AUTH[@]}" "$API/stats" | jq -e '.products == 0 and .customers == 0 and .orders == 0'
echo "PASS: PostgreSQL CRUD Create/Read/Update/Delete, access token, FK protection, /db-health"
