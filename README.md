# Keeping Law Simple

Keeping Law Simple is the standalone app and deployment repo for
`www.keepinglawsimple.org`.

It pulls official legislative data for the 50 states, DC, and federal bills,
stores the normalized bill data in Postgres, serves the public FastAPI/Jinja
site, tracks app metrics, and can generate neutral plain-English explanations
through the shared Ollama service.

## Repo Layout

- `app/`: FastAPI app, state/federal source clients, sync service, database layer, templates, and static assets.
- `tests/`: source-client, sync, app, database, analytics, and utility tests.
- `k8s/`: production Kubernetes manifests for the KLS namespace, web app, Postgres, sync CronJob, metrics, and legacy/backfill jobs.
- `scripts/migrate_kls_sqlite_to_postgres.py`: one-time migration helper for the old SQLite data store.
- `docs/state-source-inventory.md`: current source inventory for all state integrations.

## Local Run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m app.cli init-db
python -m app.cli sync --years 2026 --limit 5
uvicorn app.main:app --reload
```

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

## Container Image

```powershell
docker build -t registry.skazproconsulting.com/keeping-law-simple/web:latest .
docker push registry.skazproconsulting.com/keeping-law-simple/web:latest
```

## Kubernetes Production Deploy

The KLS repo owns the app manifests. Shared platform dependencies still live in
the platform/Kubernetes repo:

- Gateway API parent `gateway-system/skazpro-public` with KLS root and www sections.
- `ollama-router.ollama.svc.cluster.local` for interpretation generation.
- `truenas-csi-iscsi-rwo` storage class for Postgres.
- Prometheus Operator/Grafana sidecar for `ServiceMonitor` and dashboard config maps.
- The automation namespace Godaddy API if applying the optional domain sync CronJob.

Create real Kubernetes secrets outside Git before applying the manifests:

- `keeping-law-simple-secrets`: API keys/admin/analytics settings.
- `keeping-law-simple-postgres-secrets`: Postgres password. Start from `k8s/postgres-secret.example.yaml`.

Apply the current production app set:

```bash
kubectl apply -k k8s
kubectl create job --from=cronjob/keeping-law-simple-sync keeping-law-simple-sync-now -n keeping-law-simple
```

The production sync CronJob is an Indexed Job with 52 completions and
`parallelism: 8`, so each state/DC/federal sync has its own pod while cluster
load stays bounded.

## Secret Policy

Do not commit live secrets, SQLite data files, database dumps, kubeconfigs, or
local environment files. This repo intentionally keeps only example secret
manifests and source-controlled deployment templates.
