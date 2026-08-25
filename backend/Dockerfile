# FamilyGraph API 镜像（多阶段：构建安装与运行环境分离）
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY --from=builder /install /usr/local
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

EXPOSE 8000
# m0b：启动前先应用 Alembic 迁移（空库自举；幂等），再对外服务
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
