FROM bitnami/spark:3.5

USER root
RUN pip install --no-cache-dir \
    pyyaml \
    boto3 \
    psycopg2-binary

COPY jobs/ /opt/spark/jobs/
COPY config.yaml /opt/spark/config.yaml

USER 1001
