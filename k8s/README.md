# Khaya on Kubernetes

Plain, transparent manifests for a home cluster: one PostgreSQL StatefulSet
with its own persistent volume, a one-shot Job that provisions the schema,
and one Deployment of the dashboard. No operators or CRDs are required.

```
kubectl apply -f k8s/          # after editing the secret (see below)
```

## Layout

| File | Purpose |
| --- | --- |
| `00-namespace.yaml` | `khaya` namespace for everything below |
| `10-db-secret.yaml` | PostgreSQL credentials shared by the database and app |
| `20-db-service.yaml` | Headless service: `khaya-db` DNS name for PostgreSQL |
| `30-db-statefulset.yaml` | PostgreSQL 17 pod with a persistent volume claim |
| `40-db-init-job.yaml` | One-shot `python -m db init` against the database |
| `50-app-deployment.yaml` | Dashboard + collector, reaching the database over the network |
| `60-app-service.yaml` | ClusterIP service for in-cluster consumers |

## Before first apply

1. Build and load the dashboard image so the cluster can pull it:

   ```sh
   docker build -t khaya:latest .
   docker save khaya:latest | sudo k3s ctr images import -   # k3s example
   # kind:  kind load docker-image khaya:latest
   # minikube:  minikube image load khaya:latest
   ```

2. Create the database secret with a generated password (preferred over
   editing `10-db-secret.yaml` and committing a real password):

   ```sh
   kubectl apply -f k8s/00-namespace.yaml
   kubectl -n khaya create secret generic khaya-db-credentials \
     --from-literal=POSTGRES_USER=khaya \
     --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)" \
     --from-literal=POSTGRES_DB=khaya
   ```

   If you skip this, `kubectl apply -f k8s/` will create the placeholder
   secret from `10-db-secret.yaml`; replace `CHANGE_ME...` with a real
   password before the database starts.

3. Apply everything:

   ```sh
   kubectl apply -f k8s/
   kubectl -n khaya get pods -w
   ```

   The init Job waits (up to two minutes) for PostgreSQL to accept
   connections, creates the schema, and completes. The dashboard becomes
   ready right after.

## Reaching the dashboard

The deployment publishes a **hostPort 8765**, so the dashboard answers on
port 8765 of every node's LAN address — `http://<node-lan-ip>:8765` — which
keeps the app's private-address and same-origin request checks intact.

Cluster-internal consumers can use the `khaya` service. To publish on the
LAN differently, a LoadBalancer (for example MetalLB) requesting port 8765
preserves the Host header checks; a NodePort in the 30000 range does not
unless your API server widens `--service-node-port-range`.

The app connects to the database through `KHAYA_DATABASE_URL`, assembled in
each manifest from the secret as
`postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@khaya-db:5432/$(POSTGRES_DB)`.

## Day-2 operations

All database commands run without the dashboard, using the same image:

```sh
# Inspect the database
kubectl -n khaya run db-status --rm -it --restart=Never --image=khaya:latest \
  --env=KHAYA_DATABASE_URL=postgresql://khaya:PASSWORD@khaya-db:5432/khaya \
  -- python3 -m db status

# Take a portable backup (then copy it out and delete the pod)
kubectl -n khaya run db-backup --restart=Never --image=khaya:latest \
  --env=KHAYA_DATABASE_URL=postgresql://khaya:PASSWORD@khaya-db:5432/khaya \
  -- python3 -m db backup --output /tmp/khaya-backup.json
kubectl -n khaya cp db-backup:/tmp/khaya-backup.json ./khaya-backup.json
kubectl -n khaya delete pod db-backup

# Restore one (replaces all data; --yes confirms)
kubectl -n khaya run db-restore --rm -it --restart=Never --image=khaya:latest \
  --env=KHAYA_DATABASE_URL=postgresql://khaya:PASSWORD@khaya-db:5432/khaya \
  -- python3 -m db restore --input - --yes < ./khaya-backup.json
```

SQLite is not supported by this image. To move an old `data/network.db`
inventory, use an older Khaya release to run `python3 -m db backup`, then
restore that JSON dump with this image.

To change the database password later: update the secret, then restart the
StatefulSet and deployment (`kubectl -n khaya rollout restart statefulset/khaya-db deployment/khaya`)
and re-run the init Job if it was recreated.

## Storage and sizing

The database claim requests **2Gi** on the default StorageClass; a home
inventory with capped observations stays far below that. Memory requests
(256Mi for PostgreSQL, 128Mi for the dashboard) fit comfortably on a
single-board node. Adjust `volumeClaimTemplates` and resources for your
cluster; nothing else references those values.

## Notes and limits

- Keep the dashboard at `replicas: 1`. A second replica would double ICMP
  probing and duplicate events even though writes stay serialized by the
  advisory lock.
- PostgreSQL runs as a single instance; the standalone backup commands above
  are the recovery story. If you later want automated failover and
  scheduled backups, `docs/database-options.md` lists operator-based
  alternatives that do not change application code.
- The app reaches Pi-hole (`10.0.10.53:53`) and pings LAN devices directly,
  so the pod needs normal node network access (default CNI behavior).
