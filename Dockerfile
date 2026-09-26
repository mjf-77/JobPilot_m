FROM python:3.11-slim

# PYTHONUNBUFFERED：日志实时输出（否则 docker logs 里看不到运行时输出）
# PYTHONUTF8：强制 UTF-8，避免容器里出现 Latin-1 编码问题
# TZ：容器默认 UTC，和本地时区不一致会让时间戳难排查
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    TIKTOKEN_CACHE_DIR=/app/.tiktoken

WORKDIR /app

# 先拷依赖清单再装：只要 requirements.txt 没变，改代码不会让这层缓存失效
COPY requirements.txt .
# PIP_INDEX_URL 可在构建时覆盖（国内服务器用清华源更快、更稳，避免下载中断导致构建失败）；
# --mount=type=cache 用 BuildKit 缓存 pip 下载物，requirements 变化时不必从零重下
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i "$PIP_INDEX_URL" -r requirements.txt

# tiktoken 首次使用会联网下载编码文件（openaipublic.blob.core.windows.net）；
# 部署环境访问不到该域名时，服务会在启动阶段直接崩。所以把编码文件打进镜像，运行时完全离线。
COPY .tiktoken /app/.tiktoken

COPY app ./app
COPY web ./web
COPY skills ./skills
COPY templates ./templates
COPY seed ./seed
COPY scripts ./scripts
COPY evals ./evals
COPY tests ./tests

# SQLite checkpoint 与 Chroma 向量库落在这里，运行时用卷挂载持久化
RUN mkdir -p /app/data

# 非 root 运行：即使容器被攻破，影响面也被限制在普通用户权限内
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 用标准库探活，不额外装 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

# 监听 0.0.0.0：容器内必须如此，否则宿主机访问不到
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
