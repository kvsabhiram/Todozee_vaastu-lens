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

# --- SSM agent (needed for CI deploys; safety net if the AMI lacks it) ------
# The standard AL2023 AMI ships and enables this already; installing is
# idempotent and guarantees the deploy path works even on a minimal AMI.
dnf install -y amazon-ssm-agent || true
systemctl enable --now amazon-ssm-agent || true

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

# --- post-deploy health check ---------------------------------------------
# Poll the app's own /health until it reports ok, so a bad image fails the
# deploy (the SSM command / CI step sees a non-zero exit) instead of silently
# leaving a broken container running.
ok=0
for i in $(seq 1 30); do
  if curl -fsS "http://localhost:${app_port}/health" | grep -q '"ok":true'; then
    echo "health OK after $((i*4))s"; ok=1; break
  fi
  sleep 4
done
if [ "$ok" -ne 1 ]; then
  echo "DEPLOY FAILED: /health did not become healthy" >&2
  journalctl -u vaastu-lens.service --no-pager -n 50 >&2 || true
  exit 1
fi

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
  --log-driver=awslogs \
  --log-opt awslogs-region=${aws_region} \
  --log-opt awslogs-group=${log_group} \
  --log-opt awslogs-create-group=true \
  --log-opt awslogs-stream=${container_name} \
  ${ecr_repo_url}:$${IMAGE_TAG}
ExecStop=/usr/bin/docker rm -f ${container_name}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable vaastu-lens.service

# --- first deploy -----------------------------------------------------------
/usr/local/bin/deploy-app.sh "${image_tag}"
