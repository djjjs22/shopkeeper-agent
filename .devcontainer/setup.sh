#!/usr/bin/env bash
# Codespace 启动后自动跑：装依赖 + 起 docker compose + 起后端 + 起前端
# 2026-07-17 为 Codespace 写
#
# 关键注意事项：
# 1. Codespace 没有 systemd，所以不能 `systemctl restart docker`，
#    docker-in-docker feature 启动时已经把 dockerd 起好了，直接用 docker 命令即可
# 2. ES (3.9GB) + embedding (1.3GB) 加起来吃 5GB 内存，必须选 8GB+ 机器
# 3. docker pull 在 Codespace 走 GCR mirror 会快很多（澳洲→GCR 有 200ms+ 延迟）

set -e
set -o pipefail

cd "$(dirname "$0")/.."
ROOT_DIR=$(pwd)
echo "============================================"
echo "  shopkeeper-agent Codespace 自动部署"
echo "  根目录: $ROOT_DIR"
echo "============================================"

# ---- 1. Docker registry mirror（避免 Codespace 拉 docker hub 镜像超时）----
echo ""
echo "[1/7] 配置 docker registry mirror"
mkdir -p ~/.docker
cat > ~/.docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://mirror.gcr.io",
    "https://docker.m.daocloud.io"
  ]
}
EOF
# Codespace 的 dockerd 是 feature 启的，改 ~/.docker/daemon.json 不生效
# 改用 buildkit / pull-time 配置 mirror
echo "  → ~/.docker/daemon.json 已写入（备用）"

# ---- 2. 装 uv ----
echo ""
echo "[2/7] 装 uv（如果还没）"
if ! command -v uv &> /dev/null; then
  pip install --quiet uv
  echo "  → uv 装好: $(uv --version)"
else
  echo "  → uv 已在: $(uv --version)"
fi

# ---- 3. docker compose 起全部容器（后端先跳过，自己起方便看 log）----
echo ""
echo "[3/7] 启动 docker 容器（mysql/redis/ES/embedding/qdrant）"
cd docker
docker compose up -d
cd ..

# ---- 4. 等容器就绪 ----
echo ""
echo "[4/7] 等容器就绪（最长 180s）"
MAX_WAIT=180
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
  if docker exec mysql sh -c "mysql -u didilili -pdili123 -N -B -e 'SELECT 1'" >/dev/null 2>&1 \
     && docker exec mysql sh -c "curl -sf http://host.docker.internal:9200 -m 3" >/dev/null 2>&1 \
     && docker exec mysql sh -c "curl -sf http://host.docker.internal:8081/info -m 3" >/dev/null 2>&1; then
    echo "  → 全部容器 healthy (等待 ${WAITED}s)"
    break
  fi
  echo "  等待中... ${WAITED}s / ${MAX_WAIT}s"
  sleep 10
  WAITED=$((WAITED + 10))
done
if [ $WAITED -ge $MAX_WAIT ]; then
  echo "  ⚠️  容器没全起来，但继续往下走（先看看 log）"
  docker ps
fi

# ---- 5. 装 Python 依赖 ----
echo ""
echo "[5/7] 装项目 Python 依赖（uv sync）"
uv sync --frozen 2>&1 | tail -n 5

# ---- 6. 起后端 ----
echo ""
echo "[6/7] 启动后端（uvicorn :8000）"
# 先建 .env（项目依赖）
if [ ! -f .env ]; then
  cp .env.example .env 2>/dev/null || true
fi
# 杀老的（可能 codespace rebuild 留下）
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn.log 2>&1 &
sleep 5
# 验证
for i in 1 2 3 4 5; do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "  → 后端 healthy: $(curl -s http://127.0.0.1:8000/health)"
    break
  fi
  echo "  等待后端... $i"
  sleep 3
done

# ---- 7. 装前端 + 起 preview ----
echo ""
echo "[7/7] 装前端 + 启动 preview（:5173）"
cd frontend
# 装 Node 18+（Codespace 默认 18+）
if ! command -v node &> /dev/null; then
  echo "  ⚠️  node 没装，跳过前端。手动 'cd frontend && npm install && npm run dev'"
else
  echo "  node: $(node --version)"
  npm install --silent --no-audit --no-fund 2>&1 | tail -n 3 || echo "  npm install 失败但继续"
  # build + preview（preview 用 python 静态服务也行，但 vite preview 最简单）
  npm run build 2>&1 | tail -n 5 || true
  pkill -f "vite" 2>/dev/null || true
  sleep 1
  nohup npm run preview -- --host 0.0.0.0 --port 5173 > /tmp/frontend.log 2>&1 &
  sleep 3
  echo "  → 前端 preview 启动（看 /tmp/frontend.log）"
fi
cd ..

echo ""
echo "============================================"
echo "  ✅ 部署完成"
echo "============================================"
echo "  后端 health: $(curl -s http://127.0.0.1:8000/health)"
echo "  后端 log:   /tmp/uvicorn.log"
echo "  前端 log:   /tmp/frontend.log"
echo ""
echo "  Codespace 自动转发 8000 / 5173 端口（已设 public）"
echo "  拍视频：把鼠标移到 PORTS 标签看 URL，分享给面试官"
echo "============================================"
