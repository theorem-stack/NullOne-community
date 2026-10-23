# Operator runbook

From the repository root, publish an immutable Linux AMD64 binary:

```bash
scripts/publish-s3-binary.sh \
  --region us-east-1 \
  --bucket <results-bucket> \
  --version v1.8.0
```

Optional flags: `--region`, `--skip-build`, `--help`.

Pass the destination bucket on the command line. Keep API keys, PEM files, and account identifiers out of the repo. See `scripts/publish-s3-binary.sh` and `docs/NULLONE_S3_RELEASE_SPEC.md`.
