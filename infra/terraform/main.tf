terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  backend "s3" {
    bucket = "bird-classification-tfstate"
    key    = "terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "bird-classification"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ── S3 Buckets ──────────────────────────────────────────

module "s3" {
  source      = "./modules/s3"
  environment = var.environment
  project     = var.project
}

# ── Kinesis Streams (replaces Kafka in AWS) ─────────────

module "kinesis" {
  source      = "./modules/kinesis"
  environment = var.environment
  project     = var.project
}

# ── SageMaker Model Serving ─────────────────────────────

module "sagemaker" {
  source      = "./modules/sagemaker"
  environment = var.environment
  project     = var.project

  model_s3_uri = "s3://${module.s3.models_bucket_name}/bird_classifier/model.tar.gz"
  role_arn     = module.sagemaker.execution_role_arn
}

# ── Lambda Batch Inference ──────────────────────────────

module "lambda" {
  source      = "./modules/lambda"
  environment = var.environment
  project     = var.project

  s3_bucket_raw      = module.s3.raw_bucket_name
  sagemaker_endpoint = module.sagemaker.endpoint_name
}

# ── ECS Fargate (API + Extractor) ───────────────────────

module "ecs" {
  source      = "./modules/ecs"
  environment = var.environment
  project     = var.project

  vpc_id             = var.vpc_id
  private_subnet_ids = var.private_subnet_ids
  public_subnet_ids  = var.public_subnet_ids

  catalog_image   = var.catalog_image
  extractor_image = var.extractor_image

  s3_bucket_raw = module.s3.raw_bucket_name

  # These should come from dedicated RDS and OpenSearch modules.
  # For now, pass empty strings — fill in when those modules are created.
  db_url        = ""
  es_url        = ""
  kafka_brokers = module.kinesis.raw_frames_stream_name
}
