# infra/ — Cloud setup & ops

Everything for standing up and running the heavy tier. **No secrets committed** — the API key and bucket names are environment/user-supplied (this repo is public).

Contents (to be added):
- EC2 g5.xlarge provisioning notes (AMI, drivers, checkpoints, security group)
- S3 bucket setup: `clips/` + `meshes/` prefixes, CORS, lifecycle expiry, IAM least-privilege
- Presigned upload/download config
- `start`/`stop` helpers + idle auto-stop watchdog
- Spot vs on-demand notes

**Spec:** [`../design/INFRA.md`](../design/INFRA.md).

## ⚠️ Public repo — never commit
- The `X-API-Key` value, AWS keys/credentials, instance IDs, bucket names you consider sensitive
- `.env` files, `*.pem` keys, model checkpoints

These are covered by the root `.gitignore`; keep them out.

_Runbook and scripts to be added during Milestone v1 Steps 2–4._
