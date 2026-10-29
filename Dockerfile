# syntax=docker/dockerfile:1

FROM python:3.13-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --prefix=/install ".[snmp]"

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /install /usr/local

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
USER 1000

ENTRYPOINT ["netbox-inventory-toolkit"]
CMD ["sync"]
