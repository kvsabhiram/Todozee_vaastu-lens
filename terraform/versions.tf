terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  # --- Remote state (recommended) -------------------------------------------
  # Uncomment and fill in once you have an S3 bucket + DynamoDB lock table.
  # Keeping state local is fine for a first apply, but switch to remote before
  # the CI pipeline runs `terraform apply` so concurrent runs don't clobber it.
  #
  # backend "s3" {
  #   bucket         = "todozee-tfstate-apsouth1"
  #   key            = "vaastu-lens/terraform.tfstate"
  #   region         = "ap-south-1"
  #   dynamodb_table = "todozee-tflock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
      Repo      = "kvsabhiram/Todozee_vaastu-lens"
    }
  }
}
