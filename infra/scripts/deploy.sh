#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT="bird-classification"

echo "=== Deploying $PROJECT ($ENVIRONMENT) to $AWS_REGION ==="

# Build and push Docker images to ECR
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_BASE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "$ECR_BASE"

for service in catalog extractor; do
  IMAGE="$ECR_BASE/$PROJECT-$ENVIRONMENT/$service:latest"
  echo "Building $service..."

  case $service in
    catalog)   docker build -t "$IMAGE" -f catalog/Dockerfile . ;;
    extractor) docker build -t "$IMAGE" -f pipeline/ingestion/Dockerfile . ;;
  esac

  echo "Pushing $IMAGE..."
  docker push "$IMAGE"
done

# Terraform apply
cd infra/terraform
terraform init
terraform apply \
  -var-file="environments/$ENVIRONMENT.tfvars" \
  -var "catalog_image=$ECR_BASE/$PROJECT-$ENVIRONMENT/catalog:latest" \
  -var "extractor_image=$ECR_BASE/$PROJECT-$ENVIRONMENT/extractor:latest" \
  -auto-approve

echo "=== Deploy complete ==="
