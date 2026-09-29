# 前端单独构建，最终镜像无需 Node；npm ci 使用锁文件保证依赖版本一致。
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 使用 Python 3.12 的精简镜像；Docker 在 Apple Silicon 上选择匹配的 ARM64 架构。
FROM python:3.12-slim
# 后续 COPY、依赖安装与运行命令都以 /app 为工作目录。
WORKDIR /app
# 不生成 pyc 缓存；标准输出即时刷新，便于 docker compose logs 查看。
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# 先复制依赖清单以复用构建缓存，改业务代码不必重新安装所有依赖。
COPY requirements.txt .
# 不在镜像中保留 pip 下载缓存，减少镜像体积。
RUN pip install --no-cache-dir -r requirements.txt
# 创建普通运行账号，API/Worker 不以 root 身份执行。
RUN useradd --create-home --uid 10001 rag
# 只复制应用代码；脚本和测试留在宿主机，.env 由 .dockerignore 排除。
COPY --chown=rag:rag app ./app
COPY --from=frontend --chown=rag:rag /frontend/dist ./app/ui
RUN mkdir -p /app/data/uploads && chown -R rag:rag /app/data
USER rag
# 声明容器内部端口，真正暴露给宿主机的地址由 compose.yaml 控制。
EXPOSE 8000
# 默认启动 API；Worker 复用此镜像但在 Compose 中覆盖启动命令。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
