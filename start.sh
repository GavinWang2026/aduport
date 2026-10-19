#!/usr/bin/env bash
# AduPort 一键启动（项目根目录）
#   ./start.sh              # Docker Compose（推荐）+ 打开浏览器
#   ./start.sh --local      # 本机 backend + frontend（开发）
#   ./start.sh --slim       # 瘦镜像 overlay
#   ./start.sh --ocr        # OCR overlay
#   ./start.sh --stop       # 停止 compose
#   ./start.sh --no-open    # 不自动打开浏览器
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

MODE="docker"
STOP=0
OPEN_BROWSER=1
EXTRA_COMPOSE=()
UI_URL="http://127.0.0.1:8081"
API_URL="http://127.0.0.1:8000"

usage() {
  cat <<'EOF'
Usage: ./start.sh [options]

  (default)     docker compose up --build -d，健康检查后打开 Web UI
  --local       本机启动：uvicorn :8000 + vite :7173（需已 pip/npm 安装）
  --slim        叠加 docker-compose.slim.yml
  --ocr         叠加 docker-compose.ocr.yml
  --stop        docker compose down
  --fg          前台运行 compose（不加 -d）
  --no-open     启动成功后不自动打开浏览器
  -h, --help    显示帮助

首次会从 .env.example 生成 .env，并写入随机 API / SESSION secret（若为空）。
Finder 双击请用「一键启动.command」。
EOF
}

DETACH="-d"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) MODE="local"; UI_URL="http://127.0.0.1:7173"; shift ;;
    --slim) EXTRA_COMPOSE+=(-f docker-compose.slim.yml); shift ;;
    --ocr) EXTRA_COMPOSE+=(-f docker-compose.ocr.yml); shift ;;
    --stop) STOP=1; shift ;;
    --fg) DETACH=""; shift ;;
    --no-open) OPEN_BROWSER=0; shift ;;
    --open) OPEN_BROWSER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

_rand() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    python3 -c 'import secrets; print(secrets.token_hex(24))'
  fi
}

_open_url() {
  local url="$1"
  [[ "$OPEN_BROWSER" -eq 1 ]] || return 0
  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v start >/dev/null 2>&1; then
    start "$url" >/dev/null 2>&1 || true
  fi
}

_alert() {
  local title="$1" msg="$2"
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"$title\" message \"$msg\"" >/dev/null 2>&1 || true
  fi
}

_ensure_env() {
  if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
      cp .env.example .env
      echo "Created .env from .env.example"
    else
      echo "Missing .env and .env.example" >&2
      exit 1
    fi
  fi
  local api sess
  api=$(grep -E '^ADUPORT_API_KEY=' .env | head -1 | cut -d= -f2- || true)
  sess=$(grep -E '^ADUPORT_SESSION_SECRET=' .env | head -1 | cut -d= -f2- || true)
  if [[ -z "${api}" ]]; then
    api="$(_rand)"
    if grep -qE '^ADUPORT_API_KEY=' .env; then
      if sed --version >/dev/null 2>&1; then
        sed -i -E "s|^ADUPORT_API_KEY=.*|ADUPORT_API_KEY=${api}|" .env
      else
        sed -i '' -E "s|^ADUPORT_API_KEY=.*|ADUPORT_API_KEY=${api}|" .env
      fi
    else
      printf 'ADUPORT_API_KEY=%s\n' "$api" >> .env
    fi
    echo "Generated ADUPORT_API_KEY (save it): $api"
  fi
  if [[ -z "${sess}" || ${#sess} -lt 16 ]]; then
    sess="$(_rand)"
    if grep -qE '^ADUPORT_SESSION_SECRET=' .env; then
      if sed --version >/dev/null 2>&1; then
        sed -i -E "s|^ADUPORT_SESSION_SECRET=.*|ADUPORT_SESSION_SECRET=${sess}|" .env
      else
        sed -i '' -E "s|^ADUPORT_SESSION_SECRET=.*|ADUPORT_SESSION_SECRET=${sess}|" .env
      fi
    else
      printf 'ADUPORT_SESSION_SECRET=%s\n' "$sess" >> .env
    fi
    echo "Generated ADUPORT_SESSION_SECRET (≥16)"
  fi
  if ! grep -qE '^ADUPORT_REQUIRE_AUTH=' .env; then
    printf 'ADUPORT_REQUIRE_AUTH=1\n' >> .env
  fi
}

_compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

# Bash 3.2 (macOS) + set -u: empty "${arr[@]}" → unbound variable.
# Use ${arr[@]+"${arr[@]}"} so zero elements expand to nothing.
if [[ "$STOP" -eq 1 ]]; then
  # shellcheck disable=SC2086
  _compose -f docker-compose.yml ${EXTRA_COMPOSE[@]+"${EXTRA_COMPOSE[@]}"} down
  echo "Stopped."
  exit 0
fi

_ensure_env

if [[ "$MODE" == "docker" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "未检测到 Docker。请先安装 Docker Desktop，或使用本机模式：./start.sh --local" >&2
    _alert "AduPort 启动失败" "未检测到 Docker。请先安装并启动 Docker Desktop，然后再双击「一键启动」。"
    exit 1
  fi
  # Docker Desktop may be installed but daemon not running
  if ! docker info >/dev/null 2>&1; then
    echo "Docker 已安装但未运行，尝试打开 Docker Desktop…" >&2
    if [[ -d "/Applications/Docker.app" ]]; then
      open -a Docker >/dev/null 2>&1 || true
      echo "等待 Docker 引擎就绪…"
      for _ in $(seq 1 60); do
        if docker info >/dev/null 2>&1; then
          break
        fi
        sleep 2
      done
    fi
    if ! docker info >/dev/null 2>&1; then
      _alert "AduPort 启动失败" "请先打开 Docker Desktop，等待其完全启动后再双击「一键启动」。"
      exit 1
    fi
  fi

  echo "正在启动 AduPort（首次可能需几分钟下载/构建）…"
  # shellcheck disable=SC2086
  _compose -f docker-compose.yml ${EXTRA_COMPOSE[@]+"${EXTRA_COMPOSE[@]}"} up --build $DETACH
  if [[ -n "$DETACH" ]]; then
    echo "等待服务就绪…"
    for _ in $(seq 1 90); do
      if curl -sf "${API_URL}/api/health" >/dev/null 2>&1; then
        echo "OK — 正在打开 Web UI…"
        echo "  Web UI : ${UI_URL}"
        echo "  API    : ${API_URL}"
        echo "  停止   : 双击「一键停止.command」或 ./start.sh --stop"
        _open_url "$UI_URL"
        exit 0
      fi
      sleep 2
    done
    echo "健康检查超时 — 请查看：docker compose logs --tail=80" >&2
    _alert "AduPort 启动超时" "服务未在预期时间内就绪。请打开终端运行：docker compose logs --tail=80"
    exit 1
  fi
  exit 0
fi

# ---- local mode ----
echo "Starting local backend + frontend…"
if [[ ! -d backend ]]; then
  echo "Run from repo root" >&2
  exit 1
fi

export ADUPORT_REQUIRE_AUTH="${ADUPORT_REQUIRE_AUTH:-0}"
export ADUPORT_DEV_OK="${ADUPORT_DEV_OK:-1}"
export ADUPORT_DEBUG="${ADUPORT_DEBUG:-1}"

set -a
# shellcheck disable=SC1091
source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | sed 's/\r$//' || true)
set +a

PIDS=()
cleanup() {
  if ((${#PIDS[@]} > 0)); then
    for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  fi
}
trap cleanup EXIT INT TERM

(
  cd backend
  if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  exec python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
) &
PIDS+=($!)

(
  cd frontend
  if [[ ! -d node_modules ]]; then
    npm ci || npm install
  fi
  exec npm run dev -- --host 127.0.0.1 --port 7173
) &
PIDS+=($!)

echo "等待前端就绪…"
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:7173" >/dev/null 2>&1 \
    || curl -sf "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
    _open_url "$UI_URL"
    break
  fi
  sleep 1
done

echo "Backend  http://127.0.0.1:8000"
echo "Frontend http://127.0.0.1:7173"
echo "Ctrl+C to stop"
wait
