terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
  # Configure a remote backend (S3 + DynamoDB lock or S3 native locking) per environment.
  # backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge(var.tags, { Project = var.name, Environment = var.environment })
  }
}
