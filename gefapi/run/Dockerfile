ARG  ENVIRONMENT
ARG  ENVIRONMENT_VERSION
FROM conservationinternational/${ENVIRONMENT}:${ENVIRONMENT_VERSION}

USER root

COPY src /project/gefcore/script
COPY requirements.txt /project/requirements.txt

RUN chmod -R a+rX /project/gefcore/script

RUN pip install --no-cache-dir -r /project/requirements.txt

USER $USER
