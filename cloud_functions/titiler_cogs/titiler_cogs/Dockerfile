FROM public.ecr.aws/lambda/python:3.14@sha256:497df5d229e6d5c54279a6a48e7f597ff386bd02a9b3b37e788bb881108fc081 AS runtime
RUN dnf install -y expat && dnf clean all
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN python -m pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt --target ${LAMBDA_TASK_ROOT}
RUN python -m pip check
RUN cp /usr/lib64/libexpat.so.1 ${LAMBDA_TASK_ROOT}/
COPY app.py ${LAMBDA_TASK_ROOT}/app.py
RUN python -c "from app import handler"
CMD ["app.handler"]

FROM runtime AS test
COPY tests ${LAMBDA_TASK_ROOT}/tests
RUN PUBLIC_SHARES_S3_BUCKET=private-webmap-shares python -m unittest discover -s tests -v

FROM runtime AS production
