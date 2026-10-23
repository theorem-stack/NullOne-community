# PRD: NullOne Docker Runtime

**Status:** Draft  
**Repo:** `NullOne`  
**Companion repo:** `source-latency-profiler`

---

## 1. Overview

NullOne should publish immutable Docker images that can be deployed by the source-latency-profiler control plane. Docker is the maintained runtime artifact format; secrets and event-specific runtime configuration remain external.

## 2. Goals

- Build a reproducible NullOne image from repository source.
- Publish immutable tags to Amazon ECR.
- Keep `.env`, PEM files, and trading secrets out of the image.
- Accept runtime configuration from `/etc/nullone/runtime.json`.
- Accept secrets from `/run/secrets`.
- Support:
  - `nullone health --config /etc/nullone/runtime.json`
  - `nullone ready --config /etc/nullone/runtime.json`
  - `nullone execute --config /etc/nullone/runtime.json`
  - `nullone shutdown --config /etc/nullone/runtime.json`

## 3. Image Contract

The image should:

- use Python 3.12
- install the `nullone` console script
- set `ENTRYPOINT ["nullone"]`
- include no secrets
- include no event-specific profile files as the source of truth

The runtime host mounts:

```text
/etc/nullone/runtime.json
/run/secrets/nullone.env
/run/secrets/kalshi-private-key.pem
```

## 4. GitHub Actions Build

The Docker workflow lives at `.github/workflows/build-docker-image.yml`.

GitHub Actions should:

1. Run tests.
2. Authenticate to AWS using OIDC.
3. Create the ECR repo if missing.
4. Build `linux/amd64` image.
5. Push immutable tag.
6. Emit image manifest JSON.

### 4.1 Workflow Triggers

| Trigger | Behavior |
|---------|----------|
| Pull request to `main` | Run tests and build Docker image only. Do not authenticate to AWS or push. |
| Push/merge to `main` | Run tests, build image, push to ECR using the short git SHA tag. |
| Tag push matching `nullone-runtime-*` | Run tests, build image, push to ECR using the short git SHA tag unless manually overridden. |
| Manual `workflow_dispatch` | Run tests, build image, push to ECR. Optional `image_tag` input overrides the default short SHA tag. |

PRs intentionally do not receive AWS credentials. This keeps external or review branches from publishing deployable images.

### 4.2 Required GitHub Secret

The repository must define this GitHub Actions secret:

```text
AWS_GITHUB_ACTIONS_ROLE_ARN
```

The value is the AWS IAM role ARN GitHub Actions can assume via OIDC:

```text
arn:aws:iam::<AWS_ACCOUNT_ID>:role/<ROLE_NAME>
```

The role must trust the GitHub OIDC provider for this repository and allow ECR operations needed to create the repository, authenticate, upload layers, and push images.

### 4.3 Recommended Tag Format

Do not use `latest` for production event runs.

Recommended Docker image tags:

| Tag | When | Example |
|-----|------|---------|
| Short git SHA | Default immutable build tag | `30c424fabc12` |
| Runtime release tag | Manual runtime image release | `nullone-runtime-2026-05-25` |
| Event-specific tag | Manual event deployment | `wngsr-2026-05-29-30c424f` |

The default workflow tag is the first 12 characters of `GITHUB_SHA`:

```bash
IMAGE_TAG="${IMAGE_TAG_INPUT:-${GITHUB_SHA::12}}"
```

Use short SHA tags for normal mainline deployments. Use event-specific tags when handing an image to an external orchestrator for a named event.

### 4.4 Automatic Publish on Merge

When a PR is merged to `main`, the workflow runs on the resulting `push` event and publishes an image to ECR automatically.

The resulting image URI is written to the uploaded `nullone-image-manifest` artifact:

```json
{
  "schema_version": 1,
  "artifact_type": "container",
  "engine": "nullone",
  "image": "<account>.dkr.ecr.us-east-1.amazonaws.com/nullone:<short-sha>",
  "git_sha": "<full-sha>",
  "created_at": "2026-05-25T16:00:00Z"
}
```

### 4.5 Manual Build and Push from GitHub

Use this when you want a human-readable release or event tag.

1. Open GitHub → `theorem-stack/NullOne`.
2. Go to **Actions**.
3. Select **Build NullOne Docker Image**.
4. Click **Run workflow**.
5. Choose the branch, usually `main`.
6. Optional: enter an `image_tag`.
7. Click **Run workflow**.

Recommended manual `image_tag` examples:

```text
nullone-runtime-2026-05-25
wngsr-2026-05-29-30c424f
paper-validation-2026-05-25
```

After the workflow completes:

1. Open the completed workflow run.
2. Download the `nullone-image-manifest` artifact.
3. Use the `image` field as the immutable deployment image URI.

### 4.6 Local Validation Before Push

Before merging or manually dispatching a build, validate locally:

```bash
python -m pip install -e ".[dev]"
python -m pytest
docker build --platform linux/amd64 -t nullone:local-test .
docker run --rm nullone:local-test next-release
```

## 5. Manifest

```json
{
  "schema_version": 1,
  "artifact_type": "container",
  "engine": "nullone",
  "image": "<AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/nullone:a1b2c3d",
  "git_sha": "a1b2c3d",
  "created_at": "2026-05-25T16:00:00Z"
}
```

## 6. Runtime Shape

The control plane should start one long-lived container during warmup and execute commands inside it. The hot path should not require a fresh image pull or fresh container creation.

```bash
docker start nullone-runtime
docker exec nullone-runtime nullone ready --config /etc/nullone/runtime.json
docker exec nullone-runtime nullone execute --config /etc/nullone/runtime.json
```

## 7. Risks

| Risk | Mitigation |
|------|------------|
| Secrets baked into image | `.dockerignore`, CI checks, and external Secrets Manager |
| `latest` tag deployed accidentally | Control plane rejects `latest` by default |
| Fresh container start on hot path | Runtime host uses long-lived container and `docker exec` |
| Image pull variance during warmup | Pull image immediately after EC2 boot and before readiness |
