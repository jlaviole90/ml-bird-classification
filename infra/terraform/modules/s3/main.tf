variable "environment" { type = string }
variable "project" { type = string }

locals {
  prefix = "${var.project}-${var.environment}"
}

resource "aws_s3_bucket" "raw_frames" {
  bucket = "${local.prefix}-raw-frames"
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_frames_lifecycle" {
  bucket = aws_s3_bucket.raw_frames.id

  rule {
    id     = "glacier-after-90d"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = 365
    }
  }
}

resource "aws_s3_bucket_versioning" "raw_frames" {
  bucket = aws_s3_bucket.raw_frames.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket" "training_data" {
  bucket = "${local.prefix}-training-data"
}

resource "aws_s3_bucket" "model_artifacts" {
  bucket = "${local.prefix}-model-artifacts"
}

resource "aws_s3_bucket_versioning" "model_artifacts" {
  bucket = aws_s3_bucket.model_artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

output "raw_bucket_name" {
  value = aws_s3_bucket.raw_frames.bucket
}

output "raw_bucket_arn" {
  value = aws_s3_bucket.raw_frames.arn
}

output "training_bucket_name" {
  value = aws_s3_bucket.training_data.bucket
}

output "models_bucket_name" {
  value = aws_s3_bucket.model_artifacts.bucket
}
