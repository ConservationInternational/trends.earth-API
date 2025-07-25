FROM python:3.11-alpine
LABEL maintainer="Trends.Earth Team <info@trends.earth>"

ENV NAME=gef-api
ENV USER=gef-api

RUN apk update && apk upgrade && \
   apk add --no-cache --update bash git openssl-dev build-base alpine-sdk \
   libffi-dev postgresql-dev postgresql-client gcc python3-dev musl-dev

RUN addgroup $USER && adduser -s /bin/bash -D -G $USER $USER

# Add user to docker group for Docker socket access
RUN addgroup docker && adduser $USER docker

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
COPY generate_swagger.py ./generate_swagger.py
COPY run_db_migrations.py ./run_db_migrations.py
COPY ./migrations ./migrations
COPY ./tests ./tests
COPY pytest.ini .
RUN chown -R $USER:$USER /opt/$NAME

# Switch to non-root user for security
USER $USER

ENTRYPOINT ["./entrypoint.sh"]
