FROM python:3.11-alpine
LABEL maintainer="Trends.Earth Team <info@trends.earth>"

ENV NAME=gef-api
ENV USER=gef-api

RUN apk update && apk upgrade && \
   apk add --no-cache --update bash git openssl-dev build-base alpine-sdk \
   libffi-dev postgresql-dev postgresql-client gcc python3-dev musl-dev \
   geos-dev gdal-dev proj-dev proj-util

RUN addgroup $USER && adduser -s /bin/bash -D -G $USER $USER

# Create docker group with a common GID that matches most systems
# The entrypoint script will handle any GID mismatches
RUN addgroup -g 999 docker || addgroup docker
RUN adduser $USER docker

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
COPY run_db_migrations.py ./run_db_migrations.py
COPY deployment_utils.py ./deployment_utils.py
COPY ./migrations ./migrations
COPY ./tests ./tests
COPY setup_staging_environment.py ./setup_staging_environment.py
COPY pytest.ini .
RUN chown -R $USER:$USER /opt/$NAME

# Switch to non-root user for security
USER $USER

ENTRYPOINT ["./entrypoint.sh"]
