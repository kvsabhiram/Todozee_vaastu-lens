# Deployment — Vaastu-Lens

FastAPI + YOLOv8 room classifier, containerized and deployed to a single
AWS EC2 host in **ap-south-1 (Mumbai)**, with CI/CD via GitHub Actions.

```
 GitHub push (main)
        │
        ▼
 GitHub Actions ──OIDC──▶ AWS  ── build & push image ──▶  ECR
        │                                                   │
        └────────── ssm send-command ───────────────▶  EC2 (Docker)
                                                       pulls :sha, restarts
                                                       systemd service
                                                            │
                                                       http://<eip>:5004
```

Pieces:

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage CPU image; bakes in `yolov8m.pt` so runtime is offline. |
| `terraform/` | ECR repo, EC2 host + IAM, security group, EIP, GitHub OIDC role. |
| `.github/workflows/ci-cd.yml` | Build → smoke-test → push to ECR → redeploy via SSM. |

---

## 1. One-time infrastructure (Terraform)

Prereqs: Terraform ≥ 1.5, AWS credentials with admin-ish rights for the first
apply, Docker (only if you want to seed an image before the first deploy).

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # tweak if needed
terraform init
terraform apply
```

This creates everything and boots the EC2 host. The host's `user_data` installs
Docker + a systemd service and tries to pull `vaastu-lens:latest` from ECR. On a
brand-new account that tag doesn't exist yet, so the service will fail until the
first CI run pushes an image — that's expected.

Grab the outputs:

```bash
terraform output
#   ecr_repository_url      = 123456789.dkr.ecr.ap-south-1.amazonaws.com/vaastu-lens
#   github_actions_role_arn = arn:aws:iam::123456789:role/vaastu-lens-prod-gha-deploy
#   app_url                 = http://<elastic-ip>:5004
```

### Notes
- **Remote state**: for team use / CI applies, uncomment the S3 backend block in
  `terraform/versions.tf` and create the bucket + lock table first.
- **OIDC provider**: if your account already has
  `token.actions.githubusercontent.com`, set `create_oidc_provider = false`.
- **SSH**: disabled by default. Use **SSM Session Manager**
  (`aws ssm start-session --target <instance-id>`) for shell access, or set
  `allowed_ssh_cidrs` + `key_pair_name` for classic SSH.

---

## 2. Wire up GitHub Actions

In the GitHub repo, add **one** secret:

| Secret | Value |
|--------|-------|
| `AWS_DEPLOY_ROLE_ARN` | the `github_actions_role_arn` Terraform output |

No AWS access keys are stored — auth is via OIDC. The region / ECR repo name are
hardcoded in the workflow `env:` (change there if you renamed `project_name`).

---

## 3. Deploy

Just push to `main`:

```bash
git push origin main
```

The pipeline:
1. **build-test** (every push & PR) — builds the image, boots it, hits `/health`.
2. **deploy** (push to `main` only) — pushes `:latest` and `:<sha>` to ECR, then
   runs `/usr/local/bin/deploy-app.sh <sha>` on the host over SSM, which logs in,
   pulls the immutable `:<sha>` tag, and restarts the systemd service.

Verify:

```bash
curl http://<elastic-ip>:5004/health
curl -F file=@room.jpg -F language=Hindi http://<elastic-ip>:5004/classify
```

### Rollback
Re-run the deploy script with an older commit SHA via SSM (images are kept for
the last 10 builds):

```bash
aws ssm send-command --region ap-south-1 \
  --document-name AWS-RunShellScript \
  --targets "Key=tag:Deploy,Values=vaastu-lens-prod" \
  --parameters 'commands=["/usr/local/bin/deploy-app.sh <old-sha>"]'
```

---

## Local development

```bash
docker build -t vaastu-lens .
docker run --rm -p 5004:5004 vaastu-lens
# http://localhost:5004/health   and   http://localhost:5004/docs
```

---

## Production hardening (next steps, not included)

- Put an **ALB + ACM cert** in front for HTTPS on 443 (then close 5004 to the world).
- Add a **CloudWatch agent** / log group for the container logs.
- Move to **ECS Fargate** or an **ASG** if you need >1 instance or zero-downtime
  rolling deploys — the current setup is single-host (brief restart blip on deploy).
