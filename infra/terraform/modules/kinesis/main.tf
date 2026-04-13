variable "environment" { type = string }
variable "project" { type = string }

locals {
  prefix = "${var.project}-${var.environment}"
}

resource "aws_kinesis_stream" "raw_frames" {
  name             = "${local.prefix}-raw-frames"
  shard_count      = 2
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }
}

resource "aws_kinesis_stream" "enriched_metadata" {
  name             = "${local.prefix}-enriched-metadata"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }
}

output "raw_frames_stream_name" {
  value = aws_kinesis_stream.raw_frames.name
}

output "raw_frames_stream_arn" {
  value = aws_kinesis_stream.raw_frames.arn
}

output "metadata_stream_name" {
  value = aws_kinesis_stream.enriched_metadata.name
}

output "bootstrap_brokers" {
  description = "Placeholder — Kinesis does not use Kafka bootstrap; use stream name instead"
  value       = aws_kinesis_stream.raw_frames.name
}
