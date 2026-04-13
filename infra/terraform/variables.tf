variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project" {
  description = "Project name used for resource naming"
  type        = string
  default     = "bird-classification"
}

variable "vpc_id" {
  description = "VPC ID for ECS, RDS, and OpenSearch"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for backend services"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for ALB"
  type        = list(string)
}

variable "catalog_image" {
  description = "ECR image URI for the catalog API service"
  type        = string
  default     = ""
}

variable "extractor_image" {
  description = "ECR image URI for the frame extractor service"
  type        = string
  default     = ""
}
