variable "environment" { type = string }
variable "project" { type = string }
variable "s3_bucket_raw" { type = string }
variable "sagemaker_endpoint" { type = string }

locals {
  prefix = "${var.project}-${var.environment}"
}

# ── IAM Role ────────────────────────────────────────────

resource "aws_iam_role" "lambda_execution" {
  name = "${local.prefix}-lambda-inference"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${local.prefix}-lambda-policy"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "arn:aws:s3:::${var.s3_bucket_raw}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["sagemaker:InvokeEndpoint"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# ── Lambda Function ─────────────────────────────────────

resource "aws_lambda_function" "batch_inference" {
  function_name = "${local.prefix}-batch-inference"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 512

  # Placeholder — deploy the actual zip via CI/CD
  filename = "${path.module}/placeholder.zip"

  environment {
    variables = {
      SAGEMAKER_ENDPOINT = var.sagemaker_endpoint
      S3_BUCKET          = var.s3_bucket_raw
    }
  }
}

# ── S3 Trigger ──────────────────────────────────────────

resource "aws_lambda_permission" "s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.batch_inference.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::${var.s3_bucket_raw}"
}

resource "aws_s3_bucket_notification" "frame_uploaded" {
  bucket = var.s3_bucket_raw

  lambda_function {
    lambda_function_arn = aws_lambda_function.batch_inference.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "frames/"
    filter_suffix       = ".jpg"
  }

  depends_on = [aws_lambda_permission.s3_invoke]
}

output "function_name" {
  value = aws_lambda_function.batch_inference.function_name
}
