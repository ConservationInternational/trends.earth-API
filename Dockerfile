FROM python:3.11-alpine
LABEL maintainer="Alex Zvoleff azvoleff@conservation.org"

ENV NAME=gef-api
ENV USER=gef-api

RUN apk update && apk upgrade && \
   apk add --no-cache --update bash git openssl-dev build-base alpine-sdk \
   libffi-dev postgresql-dev gcc python3-dev musl-dev py3-pip

RUN addgroup $USER && adduser -s /bin/bash -D -G $USER $USER

RUN pip install --upgrade pip

RUN pip install virtualenv gunicorn gevent

RUN mkdir -p /opt/$NAME
RUN cd /opt/$NAME && virtualenv venv && source venv/bin/activate
COPY requirements.txt /opt/$NAME/requirements.txt
RUN cd /opt/$NAME && pip install -r requirements.txt

COPY entrypoint.sh /opt/$NAME/entrypoint.sh
COPY main.py /opt/$NAME/main.py
COPY gunicorn.py /opt/$NAME/gunicorn.py

# Copy the application folder inside the container
WORKDIR /opt/$NAME

COPY ./gefapi /opt/$NAME/gefapi
COPY ./migrations /opt/$NAME/migrations
COPY ./tests /opt/$NAME/tests
RUN chown $USER:$USER /opt/$NAME

USER root
#USER $USER

# Launch script
ENTRYPOINT ["./entrypoint.sh"]
