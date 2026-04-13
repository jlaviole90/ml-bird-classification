"""Spark job: compute detection analytics and write to TimescaleDB.

Aggregates detection counts by species, time-of-day, day-of-week, and computes
diversity metrics (Shannon entropy) for Grafana dashboards.
"""

from __future__ import annotations

import math
import os

import yaml
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType


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


def shannon_entropy(counts: list[int]) -> float:
    total = sum(counts)
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in counts if c > 0)


def main() -> None:
    cfg = load_config()
    pg = cfg["postgres"]

    spark = (
        SparkSession.builder
        .appName(cfg["spark"]["app_name"] + "-analytics")
        .master(cfg["spark"]["master"])
        .config("spark.jars.packages", "org.postgresql:postgresql:42.7.1")
        .getOrCreate()
    )

    jdbc_url = f"jdbc:postgresql://{pg['host']}:{pg['port']}/{pg['database']}"
    jdbc_props = {"user": pg["user"], "password": pg["password"], "driver": "org.postgresql.Driver"}

    detections = spark.read.jdbc(jdbc_url, "detections", properties=jdbc_props)
    species = spark.read.jdbc(jdbc_url, "species", properties=jdbc_props)

    joined = detections.join(species, detections["species_id"] == species["id"], "inner")

    # Detections per species
    species_counts = (
        joined
        .groupBy(F.col("common_name").alias("species"))
        .agg(
            F.count("*").alias("detection_count"),
            F.avg("confidence").alias("avg_confidence"),
            F.min("detected_at").alias("first_seen"),
            F.max("detected_at").alias("last_seen"),
        )
        .orderBy(F.desc("detection_count"))
    )
    species_counts.write.jdbc(jdbc_url, "analytics_species_counts", mode="overwrite", properties=jdbc_props)

    # Hourly detection heatmap (hour x day_of_week)
    hourly = (
        joined
        .withColumn("hour", F.hour("detected_at"))
        .withColumn("day_of_week", F.dayofweek("detected_at"))
        .groupBy("hour", "day_of_week")
        .agg(F.count("*").alias("detection_count"))
        .orderBy("day_of_week", "hour")
    )
    hourly.write.jdbc(jdbc_url, "analytics_hourly_heatmap", mode="overwrite", properties=jdbc_props)

    # Daily diversity index (Shannon entropy)
    daily_species = (
        joined
        .withColumn("date", F.to_date("detected_at"))
        .groupBy("date", F.col("common_name").alias("species"))
        .agg(F.count("*").alias("cnt"))
    )

    @F.udf(DoubleType())
    def entropy_udf(counts):
        return shannon_entropy(counts) if counts else 0.0

    daily_diversity = (
        daily_species
        .groupBy("date")
        .agg(
            F.collect_list("cnt").alias("counts"),
            F.countDistinct("species").alias("species_count"),
            F.sum("cnt").alias("total_detections"),
        )
        .withColumn("shannon_entropy", entropy_udf(F.col("counts")))
        .drop("counts")
        .orderBy("date")
    )
    daily_diversity.write.jdbc(jdbc_url, "analytics_daily_diversity", mode="overwrite", properties=jdbc_props)

    print("Analytics aggregation complete")
    species_counts.show(10, truncate=False)
    spark.stop()


if __name__ == "__main__":
    main()
