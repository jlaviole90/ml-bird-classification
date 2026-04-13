variable "environment" { type = string }
variable "project" { type = string }
variable "model_s3_uri" { type = string }
variable "role_arn" { type = string }

locals {
  prefix = "${var.project}-${var.environment}"
}

# ── IAM Role ────────────────────────────────────────────

resource "aws_iam_role" "sagemaker_execution" {
  name = "${local.prefix}-sagemaker-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "sagemaker_full" {
  role       = aws_iam_role.sagemaker_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"
}

resource "aws_iam_role_policy_attachment" "s3_read" {
  role       = aws_iam_role.sagemaker_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
}

# ── Model ───────────────────────────────────────────────

resource "aws_sagemaker_model" "bird_classifier" {
  name               = "${local.prefix}-bird-classifier"
  execution_role_arn = aws_iam_role.sagemaker_execution.arn

  primary_container {
    image          = "763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-inference:2.2.0-cpu-py311-ubuntu22.04-sagemaker"
    model_data_url = var.model_s3_uri
  }
}

# ── Endpoint Config ─────────────────────────────────────

resource "aws_sagemaker_endpoint_configuration" "bird_classifier" {
  name = "${local.prefix}-bird-classifier-config"

  production_variants {
    variant_name           = "primary"
    model_name             = aws_sagemaker_model.bird_classifier.name
    initial_instance_count = 1
    instance_type          = "ml.m5.large"
    initial_variant_weight = 1.0
  }
}

# ── Endpoint ────────────────────────────────────────────

resource "aws_sagemaker_endpoint" "bird_classifier" {
  name                 = "${local.prefix}-bird-classifier"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.bird_classifier.name
}

# ── Auto-scaling ────────────────────────────────────────

resource "aws_appautoscaling_target" "sagemaker" {
  max_capacity       = 4
  min_capacity       = 1
  resource_id        = "endpoint/${aws_sagemaker_endpoint.bird_classifier.name}/variant/primary"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  service_namespace  = "sagemaker"
}

resource "aws_appautoscaling_policy" "sagemaker_scale" {
  name               = "${local.prefix}-sagemaker-scale"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.sagemaker.resource_id
  scalable_dimension = aws_appautoscaling_target.sagemaker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.sagemaker.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }
    target_value = 100
  }
}

output "endpoint_name" {
  value = aws_sagemaker_endpoint.bird_classifier.name
}

output "execution_role_arn" {
  value = aws_iam_role.sagemaker_execution.arn
}
