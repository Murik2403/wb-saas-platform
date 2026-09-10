#!/usr/bin/env bash
set -u

OLD=wb-tenant-mvvorokov
LEGACY=wb-tenant-mvvorokov-legacy
NEW=wb-tenant-mvvorokov
NETWORK=wbsaas-tenant-mvvorokov
VOLUME=wb-tenant-data-mvvorokov
GATEWAY_INTERNAL_SECRET=$(docker exec wb-saas-gateway-1 python3 -c 'import config; from internal_auth import tenant_credential; print(tenant_credential(config.INTERNAL_API_SECRET, "mvvorokov"))')

docker rm -f "$LEGACY" 2>/dev/null || true
docker stop "$OLD"
docker rename "$OLD" "$LEGACY"

docker run -d --name "$NEW" --network "$NETWORK" --restart unless-stopped \
  --memory 2304m --cpus 1.0 \
  --mount "type=volume,src=$VOLUME,dst=/app/tenant-app/data" \
  -e WB_SAAS_TENANT_SLUG=mvvorokov \
  -e WB_SAAS_INTERNAL_API_URL=https://marketshelper.ru \
  -e WB_SAAS_INTERNAL_API_SECRET="$GATEWAY_INTERNAL_SECRET" \
  -e MH_ALLOWED_HOSTS=localhost,127.0.0.1,mvvorokov.app.marketshelper.ru \
  -e WB_SAAS_BILLING_URL=https://marketshelper.ru/billing \
  -e WB_SAAS_LOGOUT_URL=https://marketshelper.ru/logout \
  -l traefik.enable=true \
  -l traefik.docker.network="$NETWORK" \
  -l 'traefik.http.routers.tenant-mvvorokov.rule=Host(`mvvorokov.app.marketshelper.ru`)' \
  -l traefik.http.routers.tenant-mvvorokov.entrypoints=websecure \
  -l traefik.http.routers.tenant-mvvorokov.middlewares=wb-saas-auth@file \
  -l traefik.http.routers.tenant-mvvorokov.tls.certresolver=http-resolver \
  -l traefik.http.services.tenant-mvvorokov.loadbalancer.server.port=8501 \
  -l wb-saas.slug=mvvorokov \
  wb-dashboard-tenant:redesign

RUNNING=$(docker inspect -f '{{.State.Running}}' "$NEW" 2>/dev/null || true)
API=
FRONTEND=
for _ in $(seq 1 12); do
  RUNNING=$(docker inspect -f '{{.State.Running}}' "$NEW" 2>/dev/null || true)
  FRONTEND=$(docker exec "$NEW" python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8501/healthz", timeout=5).status)' 2>/dev/null || true)
  API=$(docker exec "$NEW" python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8501/api/v1/dashboard/today", timeout=15).status)' 2>/dev/null || true)
  [ "$RUNNING" = "true" ] && [ "$FRONTEND" = "200" ] && [ "$API" = "200" ] && break
  sleep 5
done
echo "new_running=$RUNNING frontend_status=$FRONTEND api_status=$API"

if [ "$RUNNING" != "true" ] || [ "$FRONTEND" != "200" ] || [ "$API" != "200" ]; then
  echo 'staging container failed; rolling back'
  docker logs "$NEW" > /root/mh-redesign-backups/mvvorokov-last-startup.log 2>&1 || true
  docker rm -f "$NEW" 2>/dev/null || true
  docker rename "$LEGACY" "$OLD"
  docker start "$OLD" >/dev/null
  exit 1
fi

docker logs "$NEW" 2>&1 | tail -n 35
curl -k -sS -o /dev/null -D - https://mvvorokov.app.marketshelper.ru/ | sed -n '1,12p'
