# syntax=docker/dockerfile:1

# 使用官方 uv 镜像：自带 uv 和 Python，依赖安装极快且可复现
FROM ghcr.io/astral-sh/uv:latest

# 项目统一放在 /app（uv 约定工作目录）
WORKDIR /app

# 先只复制依赖声明，利用 Docker 层缓存，改代码不重装依赖
COPY pyproject.toml uv.lock ./

# 按 uv.lock 严格安装依赖到项目内 .venv（--frozen 保证可复现，不解析新版本）
RUN uv sync --frozen --no-install-project

# 复制应用源码（运行时需要的目录）
COPY app ./app
COPY conf ./conf
COPY prompts ./prompts
COPY main.py ./

# 安装项目自身（把当前包注册进 .venv）
RUN uv sync --frozen

# 健康检查：探测 /health 端点，容器编排可据此重启异常实例
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD uv run python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# 启动入口：app.main:app 是带 CORS / 限流 / 请求ID 中间件的完整应用
# 运行时需通过环境变量或挂载 .env 提供 LLM_API_KEY / DB_PASSWORD / REDIS_URL 等
# 例如 docker run --env-file .env -p 8000:8000 shopkeeper-agent
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
