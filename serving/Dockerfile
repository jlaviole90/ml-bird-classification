FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends default-jre-headless curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir torchserve torch-model-archiver Pillow pyyaml

RUN useradd -m model-server
USER model-server
WORKDIR /home/model-server

COPY --chown=model-server config.properties /home/model-server/config.properties
COPY --chown=model-server model_store/ /home/model-server/model-store/

EXPOSE 8080 8081 8082

CMD ["torchserve", \
     "--start", \
     "--ncs", \
     "--model-store", "/home/model-server/model-store", \
     "--ts-config", "/home/model-server/config.properties", \
     "--foreground"]
