variable "aws_region" {
  description = "AWS region to deploy into. Mumbai per project convention."
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Short name used to prefix/tag all resources."
  type        = string
  default     = "vaastu-lens"
}

variable "environment" {
  description = "Deployment environment (e.g. prod, staging)."
  type        = string
  default     = "prod"
}

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------
variable "instance_type" {
  description = <<-EOT
    EC2 instance type. The YOLOv8m + torch (CPU) workload needs ~3-4 GB RAM
    headroom, so t3.large (8 GB) is the sensible default. Drop to t3.medium
    (4 GB) only for light traffic; go bigger for higher concurrency.
  EOT
  type        = string
  default     = "t3.large"
}

variable "root_volume_size" {
  description = "Root EBS volume size in GB. The torch+ultralytics image is large."
  type        = number
  default     = 30
}

variable "key_pair_name" {
  description = <<-EOT
    Name of an existing EC2 key pair for break-glass SSH access. Leave empty
    to launch with no SSH key (deploys go through SSM, so a key is optional).
  EOT
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Networking / access
# ---------------------------------------------------------------------------
variable "app_port" {
  description = "Container/app port the service listens on."
  type        = number
  default     = 5004
}

variable "allowed_http_cidrs" {
  description = "CIDR blocks allowed to reach the app over HTTP(S)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "allowed_ssh_cidrs" {
  description = <<-EOT
    CIDR blocks allowed to SSH (port 22). Defaults to empty = no SSH ingress.
    Set to your office/VPN IP (e.g. ["203.0.113.4/32"]) if you need shell access.
  EOT
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Container registry / image
# ---------------------------------------------------------------------------
variable "image_tag" {
  description = "Image tag the EC2 host should run on first boot."
  type        = string
  default     = "latest"
}

# ---------------------------------------------------------------------------
# CI/CD (GitHub Actions OIDC)
# ---------------------------------------------------------------------------
variable "github_repository" {
  description = "GitHub repo in 'owner/name' form, used to scope the OIDC trust."
  type        = string
  default     = "kvsabhiram/Todozee_vaastu-lens"
}

variable "github_branch" {
  description = "Branch allowed to assume the deploy role via OIDC."
  type        = string
  default     = "main"
}

variable "create_oidc_provider" {
  description = <<-EOT
    Whether to create the GitHub OIDC provider. Set false if your account
    already has token.actions.githubusercontent.com registered (only one is
    allowed per account).
  EOT
  type        = bool
  default     = true
}
