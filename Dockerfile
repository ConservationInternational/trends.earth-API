FROM python:3.11-alpine
LABEL maintainer="Alex Zvoleff azvoleff@conservation.org"

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

# Copy only poetry files first for better caching
COPY pyproject.toml poetry.lock* ./
COPY README.md ./README.md
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi

# Copy the rest of the application
COPY entrypoint.sh ./entrypoint.sh
COPY main.py ./main.py
COPY gunicorn.py ./gunicorn.py
COPY ./gefapi ./gefapi
COPY ./migrations ./migrations
COPY ./tests ./tests
RUN chown $USER:$USER /opt/$NAME

USER root
#USER $USER

ENTRYPOINT ["./entrypoint.sh"]
