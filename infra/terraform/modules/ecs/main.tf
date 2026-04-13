variable "environment" { type = string }
variable "project" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "public_subnet_ids" { type = list(string) }
variable "catalog_image" { type = string }
variable "extractor_image" { type = string }
variable "s3_bucket_raw" { type = string }
variable "db_url" { type = string }
variable "es_url" { type = string }
variable "kafka_brokers" { type = string }

locals {
  prefix = "${var.project}-${var.environment}"
}

# ── ECR Repositories ────────────────────────────────────

resource "aws_ecr_repository" "catalog" {
  name                 = "${local.prefix}/catalog"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "extractor" {
  name                 = "${local.prefix}/extractor"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ── ECS Cluster ─────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${local.prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# ── IAM ─────────────────────────────────────────────────

resource "aws_iam_role" "ecs_task_execution" {
  name = "${local.prefix}-ecs-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name = "${local.prefix}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name = "${local.prefix}-ecs-s3"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
      Resource = ["arn:aws:s3:::${var.s3_bucket_raw}", "arn:aws:s3:::${var.s3_bucket_raw}/*"]
    }]
  })
}

# ── CloudWatch Log Group ────────────────────────────────

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.prefix}"
  retention_in_days = 30
}

# ── Catalog API Task ────────────────────────────────────

resource "aws_ecs_task_definition" "catalog" {
  family                   = "${local.prefix}-catalog"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "catalog"
    image     = var.catalog_image != "" ? var.catalog_image : "${aws_ecr_repository.catalog.repository_url}:latest"
    essential = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "DATABASE_URL", value = var.db_url },
      { name = "ELASTICSEARCH_URL", value = var.es_url },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = var.kafka_brokers },
      { name = "S3_BUCKET_RAW", value = var.s3_bucket_raw },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "catalog"
      }
    }
  }])
}

resource "aws_ecs_service" "catalog" {
  name            = "${local.prefix}-catalog"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.catalog.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    assign_public_ip = false
  }
}

# ── Frame Extractor Task ────────────────────────────────

resource "aws_ecs_task_definition" "extractor" {
  family                   = "${local.prefix}-extractor"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "extractor"
    image     = var.extractor_image != "" ? var.extractor_image : "${aws_ecr_repository.extractor.repository_url}:latest"
    essential = true
    environment = [
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = var.kafka_brokers },
      { name = "S3_BUCKET_RAW", value = var.s3_bucket_raw },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "extractor"
      }
    }
  }])
}

resource "aws_ecs_service" "extractor" {
  name            = "${local.prefix}-extractor"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.extractor.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    assign_public_ip = false
  }
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "catalog_ecr_url" {
  value = aws_ecr_repository.catalog.repository_url
}

output "extractor_ecr_url" {
  value = aws_ecr_repository.extractor.repository_url
}

output "connection_url" {
  description = "Placeholder for RDS connection URL — wire up the actual RDS module"
  value       = var.db_url
}

output "endpoint" {
  description = "Placeholder for OpenSearch endpoint — wire up the actual OpenSearch module"
  value       = var.es_url
}
