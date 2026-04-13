"""Spark job: check if enough new data has accumulated to trigger model retraining.

Scans the detections table for low-confidence predictions that have been
manually reviewed, and triggers a retraining job when the threshold is met.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import boto3
import yaml
from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def _resolve_env(val):
    if isinstance(val, str) and val.startswith("${"):
        inner = val[2:-1]
        var, _, default = inner.partition(":-")
        return os.environ.get(var, default)
    return val


def _walk(obj):
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str = "pipeline/spark/config.yaml") -> dict:
    with open(path) as f:
        return _walk(yaml.safe_load(f))


def main() -> None:
    cfg = load_config()
    pg = cfg["postgres"]
    s3_cfg = cfg["s3"]
    analytics = cfg["analytics"]

    spark = (
        SparkSession.builder
        .appName(cfg["spark"]["app_name"] + "-retrain-check")
        .master(cfg["spark"]["master"])
        .config("spark.jars.packages", "org.postgresql:postgresql:42.7.1")
        .getOrCreate()
    )

    jdbc_url = f"jdbc:postgresql://{pg['host']}:{pg['port']}/{pg['database']}"
    jdbc_props = {"user": pg["user"], "password": pg["password"], "driver": "org.postgresql.Driver"}

    detections = spark.read.jdbc(jdbc_url, "detections", properties=jdbc_props)

    low_conf_threshold = float(analytics["low_confidence_threshold"])
    min_samples = int(analytics["min_samples_for_retrain"])

    low_conf_count = (
        detections
        .filter(F.col("confidence") < low_conf_threshold)
        .count()
    )

    total_count = detections.count()
    high_conf_count = (
        detections
        .filter(F.col("confidence") >= float(analytics["confidence_threshold"]))
        .count()
    )

    print(f"Total detections:          {total_count}")
    print(f"High-confidence (>={analytics['confidence_threshold']}): {high_conf_count}")
    print(f"Low-confidence (<{low_conf_threshold}):  {low_conf_count}")
    print(f"Threshold for retrain:     {min_samples}")

    should_retrain = high_conf_count >= min_samples

    if should_retrain:
        trigger_record = {
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "total_detections": total_count,
            "high_confidence_samples": high_conf_count,
            "low_confidence_samples": low_conf_count,
            "reason": f"high-confidence samples ({high_conf_count}) >= threshold ({min_samples})",
        }

        s3 = boto3.client(
            "s3",
            endpoint_url=s3_cfg["endpoint_url"],
            aws_access_key_id=s3_cfg["access_key"],
            aws_secret_access_key=s3_cfg["secret_key"],
        )
        key = f"retrain-triggers/{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        s3.put_object(
            Bucket=s3_cfg["training_bucket"],
            Key=key,
            Body=json.dumps(trigger_record, indent=2).encode(),
        )
        print(f"RETRAIN TRIGGERED — wrote trigger to s3://{s3_cfg['training_bucket']}/{key}")
    else:
        remaining = min_samples - high_conf_count
        print(f"Not enough data yet — need {remaining} more high-confidence samples")

    spark.stop()
    sys.exit(0 if not should_retrain else 10)


if __name__ == "__main__":
    main()
