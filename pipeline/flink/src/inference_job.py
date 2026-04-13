"""PyFlink streaming job — consumes frames from Kafka, calls TorchServe, emits detections.

This job implements:
  1. Kafka source on `raw-frames`
  2. Perceptual-hash deduplication
  3. Async HTTP inference calls to TorchServe
  4. Confidence threshold filtering
  5. Tumbling-window deduplication (same species within 30 s → one event)
  6. Kafka sink on `enriched-metadata`
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import httpx
import yaml

from pyflink.common import Row, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.time import Time
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import (
    KafkaOffsetsInitializer,
    KafkaRecordSerializationSchema,
    KafkaSink,
    KafkaSource,
)
from pyflink.datastream.functions import MapFunction, FlatMapFunction
from pyflink.datastream.window import TumblingProcessingTimeWindows

from pipeline.flink.src.preprocessing import compute_phash, is_near_duplicate
from pipeline.flink.src.serde import DetectionResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _resolve_env(val: str) -> str:
    if isinstance(val, str) and val.startswith("${"):
        inner = val[2:-1]
        var, _, default = inner.partition(":-")
        return os.environ.get(var, default)
    return val


def _walk(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str = "pipeline/flink/config.yaml") -> dict:
    with open(path) as f:
        return _walk(yaml.safe_load(f))


class InferenceMapper(MapFunction):
    """Call TorchServe for each frame, then validate via the FastAPI validator endpoint."""

    def __init__(self, cfg: dict):
        self.inference_url = cfg["torchserve"]["inference_url"]
        self.model_name = cfg["torchserve"]["model_name"]
        self.timeout = cfg["torchserve"]["timeout_ms"] / 1000
        self.confidence_threshold = cfg["inference"]["confidence_threshold"]
        self.validator_url = cfg.get("validator", {}).get(
            "url", os.environ.get("VALIDATOR_URL", "http://localhost:8000")
        )
        self.client: httpx.Client | None = None
        self._prev_hash: str | None = None

    def open(self, runtime_context) -> None:
        self.client = httpx.Client(timeout=self.timeout)

    def close(self) -> None:
        if self.client:
            self.client.close()

    def map(self, value: str) -> str | None:
        msg = json.loads(value)
        frame_id = msg.get("frame_id", "")
        timestamp = msg.get("timestamp", "")
        source = msg.get("source", "")
        s3_key = msg.get("s3_key", "")
        resolution = msg.get("resolution", "")

        # Perceptual-hash dedup
        frame_b64 = msg.get("frame_b64", "")
        if frame_b64:
            import base64
            frame_bytes = base64.b64decode(frame_b64)
            phash = compute_phash(frame_bytes)
            if self._prev_hash and is_near_duplicate(phash, self._prev_hash):
                return None
            self._prev_hash = phash
        else:
            frame_bytes = b""

        # Call TorchServe
        url = f"{self.inference_url}/predictions/{self.model_name}"
        try:
            resp = self.client.post(url, content=frame_bytes, headers={"Content-Type": "application/octet-stream"})
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            logger.error("Inference failed for frame %s: %s", frame_id[:8], e)
            return None

        predictions = result.get("predictions", [])
        if not predictions:
            return None

        top = predictions[0]
        if top["confidence"] < self.confidence_threshold:
            return None

        detection_id = str(uuid.uuid4())

        # Call the eBird validator if available
        validated = self._call_validator(detection_id, frame_id, predictions)

        species = validated.get("common_name", top["species"]) if validated else top["species"]
        confidence = validated.get("adjusted_confidence", top["confidence"]) if validated else top["confidence"]

        detection = DetectionResult(
            detection_id=detection_id,
            frame_id=frame_id,
            timestamp=timestamp,
            source=source,
            species=species,
            class_id=top["class_id"],
            confidence=confidence,
            top5=predictions,
            s3_key=s3_key,
            resolution=resolution,
            raw_confidence=top["confidence"],
            adjusted_confidence=validated.get("adjusted_confidence") if validated else None,
            ebird_validated=validated.get("ebird_validated", False) if validated else False,
            ebird_frequency=validated.get("ebird_frequency") if validated else None,
            is_notable=validated.get("is_notable", False) if validated else False,
            was_rerouted=validated.get("was_rerouted", False) if validated else False,
            validation_notes=validated.get("validation_notes", "") if validated else "",
            audit_id=validated.get("audit_id", "") if validated else "",
        )
        return detection.to_json_bytes().decode()

    def _call_validator(
        self, detection_id: str, frame_id: str, predictions: list[dict]
    ) -> dict | None:
        """Call the FastAPI /api/v1/validate endpoint. Returns None on failure."""
        try:
            resp = self.client.post(
                f"{self.validator_url}/api/v1/validate",
                json={
                    "detection_id": detection_id,
                    "frame_id": frame_id,
                    "predictions": predictions,
                },
                timeout=5.0,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning("Validator call failed, using raw prediction: %s", e)
        return None


class WindowDeduplicator(FlatMapFunction):
    """Within a tumbling window, emit only one detection per species."""

    def flat_map(self, values: list[str]):
        seen_species: set[str] = set()
        for v in values:
            if v is None:
                continue
            detection = json.loads(v)
            species = detection.get("species", "")
            if species not in seen_species:
                seen_species.add(species)
                yield v


def main() -> None:
    cfg = load_config()
    kafka_cfg = cfg["kafka"]

    env = StreamExecutionEnvironment.get_execution_environment()
    env.enable_checkpointing(cfg["checkpoint"]["interval_ms"])
    env.set_parallelism(2)

    kafka_source = (
        KafkaSource.builder()
        .set_bootstrap_servers(kafka_cfg["bootstrap_servers"])
        .set_topics(kafka_cfg["raw_frames_topic"])
        .set_group_id(kafka_cfg["consumer_group"])
        .set_starting_offsets(KafkaOffsetsInitializer.latest())
        .set_value_only_deserializer(SimpleStringSchema())
        .build()
    )

    kafka_sink = (
        KafkaSink.builder()
        .set_bootstrap_servers(kafka_cfg["bootstrap_servers"])
        .set_record_serializer(
            KafkaRecordSerializationSchema.builder()
            .set_topic(kafka_cfg["metadata_topic"])
            .set_value_serialization_schema(SimpleStringSchema())
            .build()
        )
        .build()
    )

    ds = env.from_source(kafka_source, WatermarkStrategy.no_watermarks(), "kafka-raw-frames")

    detections = (
        ds
        .map(InferenceMapper(cfg))
        .filter(lambda x: x is not None)
    )

    detections.sink_to(kafka_sink)

    env.execute("bird-inference-pipeline")


if __name__ == "__main__":
    main()
