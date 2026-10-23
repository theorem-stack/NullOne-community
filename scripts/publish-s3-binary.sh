#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/publish-s3-binary.sh --bucket <results-bucket> --version <version> [options]

Required:
  --bucket <name>     S3 bucket used by source-latency-profiler results
  --version <value>   Immutable release version, e.g. v1.8.0

Options:
  --region <value>    AWS region for API calls (default: us-east-1)
  --skip-build        Publish existing dist/nullone without rebuilding
  --help              Show this message

Environment overrides:
  RESULTS_BUCKET, VERSION, AWS_REGION

Behavior:
  1) Builds Linux AMD64 binary (PyInstaller in amazonlinux:2023 container)
  2) Validates executable with "./dist/nullone --help"
  3) Writes dist/nullone.sha256 as "<sha256>  nullone"
  4) Fails if target S3 key already exists (immutability guard)
  5) Uploads nullone and nullone.sha256 to:
     s3://<bucket>/nullone/releases/<version>/linux-amd64/
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUCKET="${RESULTS_BUCKET:-}"
VERSION="${VERSION:-}"
AWS_REGION="${AWS_REGION:-us-east-1}"
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)
      BUCKET="${2:-}"
      shift 2
      ;;
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --region)
      AWS_REGION="${2:-}"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$BUCKET" || -z "$VERSION" ]]; then
  echo "Error: --bucket and --version are required." >&2
  usage
  exit 2
fi

RELEASE_PREFIX="nullone/releases/${VERSION}/linux-amd64"
BINARY_KEY="${RELEASE_PREFIX}/nullone"
CHECKSUM_KEY="${RELEASE_PREFIX}/nullone.sha256"
RELEASE_URI="s3://${BUCKET}/${BINARY_KEY}"

cd "$ROOT_DIR"

echo "==> Verifying AWS caller identity"
aws sts get-caller-identity >/dev/null

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "==> Building Linux AMD64 binary in container"
  mkdir -p dist
  docker run --rm --platform linux/amd64 \
    -v "$PWD:/src" -w /src amazonlinux:2023 \
    bash -lc "set -euo pipefail; \
      dnf install -y python3.12 gcc; \
      python3.12 -m ensurepip --upgrade || true; \
      python3.12 -m pip install --upgrade pip; \
      python3.12 -m pip install -e . pyinstaller; \
      pyinstaller --clean --noconfirm nullone.spec"
fi

if [[ ! -f dist/nullone ]]; then
  echo "Error: dist/nullone not found. Build failed or --skip-build used without artifact." >&2
  exit 1
fi

echo "==> Validating executable in Linux container"
chmod +x dist/nullone
docker run --rm --platform linux/amd64 \
  -v "$PWD/dist:/dist:ro" -w /dist amazonlinux:2023 \
  bash -lc "set -euo pipefail; ./nullone --help >/dev/null"

echo "==> Generating checksum sidecar"
(
  cd dist
  shasum -a 256 nullone | awk '{print $1 "  nullone"}' > nullone.sha256
)

echo "==> Enforcing immutable release key"
if aws s3api head-object --bucket "$BUCKET" --key "$BINARY_KEY" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "Error: ${RELEASE_URI} already exists. Choose a new --version." >&2
  exit 1
fi

echo "==> Uploading binary and checksum"
aws s3 cp dist/nullone "s3://${BUCKET}/${BINARY_KEY}" --region "$AWS_REGION"
aws s3 cp dist/nullone.sha256 "s3://${BUCKET}/${CHECKSUM_KEY}" --region "$AWS_REGION"

echo "==> Verifying uploads"
aws s3api head-object --bucket "$BUCKET" --key "$BINARY_KEY" --region "$AWS_REGION" >/dev/null
aws s3api head-object --bucket "$BUCKET" --key "$CHECKSUM_KEY" --region "$AWS_REGION" >/dev/null

echo
echo "Published:"
echo "  NULLONE_INSTALL_MODE=s3"
echo "  NULLONE_S3_URI=${RELEASE_URI}"
echo "  CHECKSUM_URI=s3://${BUCKET}/${CHECKSUM_KEY}"
