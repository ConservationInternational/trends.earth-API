FROM python:3.11-alpine
LABEL maintainer="Trends.Earth Team <info@trends.earth>"

ENV NAME=gef-api
ENV USER=gef-api

RUN apk update && apk upgrade && \
   apk add --no-cache --update bash git openssl-dev build-base alpine-sdk \
   libffi-dev postgresql-dev gcc python3-dev musl-dev

RUN addgroup $USER && adduser -s /bin/bash -D -G $USER $USER

# Install Poetry
RUN pip install --upgrade pip && pip install poetry

RUN mkdir -p /opt/$NAME
WORKDIR /opt/$NAME

# Copy source code and poetry files first for dependency install
COPY ./gefapi ./gefapi
COPY pyproject.toml poetry.lock* ./
COPY README.md ./README.md

RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi

# Copy the rest of the application
COPY entrypoint.sh ./entrypoint.sh
COPY main.py ./main.py
COPY gunicorn.py ./gunicorn.py
COPY ./migrations ./migrations
COPY ./tests ./tests
RUN chown $USER:$USER /opt/$NAME

USER root
#USER $USER

ENTRYPOINT ["./entrypoint.sh"]
