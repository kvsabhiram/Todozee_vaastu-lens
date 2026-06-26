output "ecr_repository_url" {
  description = "Push images here (used by the CI pipeline)."
  value       = aws_ecr_repository.app.repository_url
}

output "app_url" {
  description = "Public URL of the running service."
  value       = "http://${aws_eip.app.public_ip}:${var.app_port}"
}

output "health_url" {
  description = "Health-check endpoint."
  value       = "http://${aws_eip.app.public_ip}:${var.app_port}/health"
}

output "instance_id" {
  description = "EC2 instance ID (used as the SSM deploy target)."
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "Elastic IP of the instance."
  value       = aws_eip.app.public_ip
}

output "github_actions_role_arn" {
  description = "Set this as the AWS_DEPLOY_ROLE_ARN secret/variable in GitHub."
  value       = aws_iam_role.github_actions.arn
}

output "aws_region" {
  value = var.aws_region
}
