FROM flink:1.18-java11

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    apache-flink==1.18.1 \
    httpx \
    pyyaml \
    imagehash \
    Pillow \
    opencv-python-headless \
    numpy

COPY src/ /opt/flink/usrlib/src/
COPY config.yaml /opt/flink/usrlib/config.yaml

ENV FLINK_PROPERTIES="jobmanager.rpc.address: flink-jobmanager"
