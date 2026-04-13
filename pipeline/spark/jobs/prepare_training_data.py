"""Spark job: prepare training data from accumulated birdcam inference results.

Reads detection metadata from PostgreSQL, filters high-confidence detections,
and writes a Parquet dataset partitioned by species to S3 for model retraining.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    s3_cfg = cfg["s3"]
    pg = cfg["postgres"]

    spark = (
        SparkSession.builder
        .appName(cfg["spark"]["app_name"] + "-training-data")
        .master(cfg["spark"]["master"])
        .config("spark.jars.packages", "org.postgresql:postgresql:42.7.1")
        .config("spark.hadoop.fs.s3a.endpoint", s3_cfg["endpoint_url"])
        .config("spark.hadoop.fs.s3a.access.key", s3_cfg["access_key"])
        .config("spark.hadoop.fs.s3a.secret.key", s3_cfg["secret_key"])
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .getOrCreate()
    )

    jdbc_url = f"jdbc:postgresql://{pg['host']}:{pg['port']}/{pg['database']}"
    detections = (
        spark.read
        .format("jdbc")
        .option("url", jdbc_url)
        .option("dbtable", "detections")
        .option("user", pg["user"])
        .option("password", pg["password"])
        .option("driver", "org.postgresql.Driver")
        .load()
    )

    species = (
        spark.read
        .format("jdbc")
        .option("url", jdbc_url)
        .option("dbtable", "species")
        .option("user", pg["user"])
        .option("password", pg["password"])
        .option("driver", "org.postgresql.Driver")
        .load()
    )

    threshold = float(cfg["analytics"]["confidence_threshold"])

    training_df = (
        detections
        .filter(F.col("confidence") >= threshold)
        .join(species, detections["species_id"] == species["id"], "inner")
        .select(
            detections["id"].alias("detection_id"),
            F.col("common_name").alias("species"),
            F.col("confidence"),
            F.col("frame_s3_key"),
            F.col("detected_at"),
            F.col("source_camera"),
        )
    )

    output_path = f"s3a://{s3_cfg['training_bucket']}/prepared"
    (
        training_df
        .repartition("species")
        .write
        .mode("overwrite")
        .partitionBy("species")
        .parquet(output_path)
    )

    row_count = training_df.count()
    species_count = training_df.select("species").distinct().count()
    print(f"Wrote {row_count} samples across {species_count} species to {output_path}")

    spark.stop()


if __name__ == "__main__":
    main()
