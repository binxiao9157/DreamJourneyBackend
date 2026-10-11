# Scoped biography Worker deployment

This optional service continuously derives prose for one explicitly authorized
Vault. It does not confirm candidates. It may process **existing active formal
facts in that Vault**, so approval of that data scope precedes activation.

Supply a protected environment file outside Git with `DATABASE_URL`, the existing
model credentials/settings, and deployment interpolation variables:

- `DJ_BIOGRAPHY_IMAGE`: verified immutable backend image digest.
- `DJ_BIOGRAPHY_ENV_FILE`: absolute path to the protected runtime environment.
- `DJ_BIOGRAPHY_VAULT_ID` and `DJ_BIOGRAPHY_OWNER_SUBJECT_ID`: explicit authorized scope.
- `DJ_BIOGRAPHY_ENABLED=true`: opt in; default is disabled.
- `DJ_BACKEND_NETWORK`: existing backend network, default `dreamjourneybackend_default`.

Use a separate Compose project, and validate without printing credentials:

```sh
docker compose --env-file /secure/biography.env -p dreamjourney-biography \
  -f deploy/biography.compose.yml --profile biography config --quiet
docker compose --env-file /secure/biography.env -p dreamjourney-biography \
  -f deploy/biography.compose.yml --profile biography up -d --no-build
```

Before activation check the frozen image/config, migration 0129, chosen account
scope, free memory and backend health. Then confirm synthetic facts through the
ordinary product and observe an automatic `ready` or bounded `fallback`, exact
formal versions and cold reads. A running container alone is not acceptance.

The service has no published port, a 1..2 connection pool, one job per polling
batch, a 30-second poll, 384 MiB memory limit and 0.5 CPU. Existing per-revision
attempt and lease rules survive restarts. Three failed restart attempts are the
limit; an exhausted service needs investigation, not repeated manual restarts.

Rollback: run the same command with `stop biography-worker`. To remove the stopped
service use `rm biography-worker` in this separate project only. Do not use the
main backend project's `down`; do not delete database tables or formal memories.
No deployment or account data is changed by adding these files.
