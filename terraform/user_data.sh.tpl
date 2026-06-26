#!/bin/bash
###############################################################################
# EC2 first-boot bootstrap for Vaastu-Lens (Amazon Linux 2023).
#   - install + start Docker
#   - install a reusable deploy script (ECR login -> pull -> restart)
#   - install a systemd unit that always runs the latest pulled image
# CI later pushes a new image and re-runs the deploy script via SSM.
#
# NOTE on escaping: this file is rendered by Terraform's templatefile(), so a
# dollar-brace token means a Terraform variable, a doubled dollar-brace emits a
# literal dollar-brace (for bash/systemd to expand later), and a plain $name is
# left untouched. The inner heredocs are single-quoted (<<'EOF') so this
# cloud-init shell does not expand them at write time; only Terraform tokens are
# substituted.
###############################################################################
set -euxo pipefail

# --- Docker -----------------------------------------------------------------
dnf update -y
dnf install -y docker
dnf install -y awscli || dnf install -y aws-cli || true
systemctl enable --now docker

# --- reusable deploy script -------------------------------------------------
cat >/usr/local/bin/deploy-app.sh <<'DEPLOY'
#!/bin/bash
set -euxo pipefail
REGION="${aws_region}"
ECR_REPO="${ecr_repo_url}"
APP_PORT="${app_port}"
CONTAINER="${container_name}"
# First arg overrides the tag; defaults to the tag baked in at provision time.
IMAGE_TAG="$${1:-${image_tag}}"
REGISTRY="$(echo "$ECR_REPO" | cut -d/ -f1)"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

docker pull "$ECR_REPO:$IMAGE_TAG"

# Record the tag systemd should run, then (re)start the service.
echo "IMAGE_TAG=$IMAGE_TAG" > /etc/vaastu-lens.env
systemctl restart vaastu-lens.service

# Reclaim disk from old layers.
docker image prune -f || true
DEPLOY
chmod +x /usr/local/bin/deploy-app.sh

# --- systemd service --------------------------------------------------------
echo "IMAGE_TAG=${image_tag}" > /etc/vaastu-lens.env

cat >/etc/systemd/system/vaastu-lens.service <<'UNIT'
[Unit]
Description=Vaastu-Lens room classifier
After=docker.service
Requires=docker.service

[Service]
EnvironmentFile=/etc/vaastu-lens.env
TimeoutStartSec=0
Restart=always
RestartSec=5
ExecStartPre=-/usr/bin/docker rm -f ${container_name}
ExecStart=/usr/bin/docker run --rm --name ${container_name} \
  -p ${app_port}:${app_port} \
  ${ecr_repo_url}:$${IMAGE_TAG}
ExecStop=/usr/bin/docker rm -f ${container_name}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable vaastu-lens.service

# --- first deploy -----------------------------------------------------------
/usr/local/bin/deploy-app.sh "${image_tag}"
