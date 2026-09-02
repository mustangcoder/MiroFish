#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
cd "$PROJECT_ROOT"

DOCKER_BIN="${DOCKER_BIN:-docker}"
CURL_BIN="${CURL_BIN:-curl}"
STARTUP_HEALTH_ATTEMPTS="${STARTUP_HEALTH_ATTEMPTS:-90}"
STARTUP_HEALTH_INTERVAL="${STARTUP_HEALTH_INTERVAL:-2}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mirofishplus}"
COMPOSE=("$DOCKER_BIN" compose -f docker-compose.yml -f docker-compose.local.yml)

if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
  echo "错误：未找到 Docker，请安装并启动 Docker Desktop。" >&2
  exit 1
fi
if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  echo "错误：Docker 当前不可用，请启动 Docker Desktop 后重试。" >&2
  exit 1
fi
if ! command -v "$CURL_BIN" >/dev/null 2>&1; then
  echo "错误：未找到 curl，无法执行服务健康检查。" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  if [[ ! -f .env.example ]]; then
    echo "错误：缺少 .env.example，无法创建本地配置。" >&2
    exit 1
  fi
  cp .env.example .env
  echo "已从 .env.example 创建 .env；模型凭据可稍后在配置中心填写。"
fi

mkdir -p backend/uploads

if [[ "${SKIP_LEGACY_DOCKER_MIGRATION:-0}" != "1" ]]; then
  legacy_containers=(
    mirofish
    mirofish-bootstrap
    mirofish-direct-oauth-gateway
    mirofish-hf-prefetch
    mirofish-neo4j
  )
  for container in "${legacy_containers[@]}"; do
    if "$DOCKER_BIN" container inspect "$container" >/dev/null 2>&1; then
      echo "停止旧容器并保留现场: $container"
      "$DOCKER_BIN" stop "$container" >/dev/null
    fi
  done

  "$SCRIPT_DIR/migrate-docker-volume.sh" mirofish_direct_oauth_credentials mirofishplus_direct_oauth_credentials
  "$SCRIPT_DIR/migrate-docker-volume.sh" mirofish_huggingface_cache mirofishplus_huggingface_cache
  "$SCRIPT_DIR/migrate-docker-volume.sh" mirofish_neo4j_data mirofishplus_neo4j_data
  "$SCRIPT_DIR/migrate-docker-volume.sh" mirofish_neo4j_logs mirofishplus_neo4j_logs
fi

diagnose_failure() {
  local status=$?
  echo "本地启动失败，当前服务状态：" >&2
  "${COMPOSE[@]}" ps -a >&2 || true
  echo "查看日志：docker compose -f docker-compose.yml -f docker-compose.local.yml logs --tail=200" >&2
  exit "$status"
}
trap diagnose_failure ERR

echo "正在构建并启动 Neo4j、Gateway、数据库初始化任务和 MiroFishPlus..."
"${COMPOSE[@]}" up -d --build

bootstrap_exit="$($DOCKER_BIN inspect -f '{{.State.ExitCode}}' mirofishplus-bootstrap)"
if [[ "$bootstrap_exit" != "0" ]]; then
  echo "错误：数据库初始化失败，bootstrap exit_code=$bootstrap_exit" >&2
  "${COMPOSE[@]}" logs --tail=200 bootstrap >&2 || true
  exit 1
fi

healthy=false
for ((attempt = 1; attempt <= STARTUP_HEALTH_ATTEMPTS; attempt++)); do
  if "$CURL_BIN" --fail --silent --show-error http://localhost:5001/health >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep "$STARTUP_HEALTH_INTERVAL"
done

if [[ "$healthy" != "true" ]]; then
  echo "错误：MiroFishPlus 后端健康检查超时。" >&2
  false
fi

trap - ERR
echo "MiroFishPlus 已启动："
echo "  前端：http://localhost:3000"
echo "  后端：http://localhost:5001/health"
echo "  Neo4j：http://localhost:7474"
