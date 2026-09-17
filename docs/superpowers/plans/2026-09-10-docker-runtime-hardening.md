# Docker Runtime Hardening Implementation Plan

> **For agentic workers:** Execute the tasks in order. Do not mount the Docker socket into an internet-facing application. Preserve tenant lifecycle behavior and validate against a running Docker Engine before declaring the change complete.

**Goal:** Remove direct host-Docker access from the gateway, run application processes as non-root with restrictive runtime settings, and preserve the current tenant provisioning, stop, cleanup, and upgrade lifecycle.

**Architecture:** Introduce a private `provisioner` service as the sole socket holder. It exposes a narrow, authenticated internal HTTP contract for the existing lifecycle operations and enforces fixed image, naming, resource, network, volume, and container-security policy server-side. The gateway becomes an unprivileged client of that contract. Traefik remains outside this change because its Docker label discovery requires read access; that separate read-only exposure is documented as residual work rather than disguised as a complete fix.

**Tech Stack:** Docker Compose, Docker SDK for Python, FastAPI/HTTPX, Python 3.12, pytest.

**Security constraints:**

- The `gateway` service must have no `/var/run/docker.sock` bind mount and no `DOCKER_HOST` that reaches the daemon.
- Only `provisioner` may mount the host socket, on an internal network with no published ports.
- The provisioner must reject arbitrary image names, host bind mounts, privileged mode, extra capabilities, host namespaces, arbitrary labels, and names outside the `wb-tenant-<slug>` convention.
- Gateway and tenant processes run as a fixed non-root UID, with `no-new-privileges`, all Linux capabilities dropped, read-only root filesystems, and explicit writable mounts only.
- Existing named volumes must be migrated to the application UID before enabling the non-root images.

---

### Task 1: Define and test the constrained provisioning contract

**Files:**
- Create: `provisioner/app.py`, `provisioner/requirements.txt`, `provisioner/Dockerfile`, `provisioner/tests/test_policy.py`
- Modify: `gateway/provisioning.py`, `gateway/tests/test_provisioning_credentials.py`

- [x] Add an internal HTTP service with only: create/start tenant, inspect health, stop/remove tenant, create/remove tenant network, create tenant volume, pull the configured tenant image, and attach/detach Traefik.
- [x] Validate slugs, generated resource names, configured image, numeric resource limits, tenant labels, and all container arguments on the provisioner side before calling Docker.
- [x] Make the gateway call this contract instead of `docker.from_env()`; preserve its retry, cleanup, status-recording, and certificate-wait behavior.
- [x] Add unit tests proving rejected image overrides, path/bind mounts, privileged settings, malformed slugs, and unexpected labels cannot reach the Docker client.

### Task 2: Harden Compose networking and service runtime

**Files:**
- Modify: `docker-compose.yml`, `gateway/Dockerfile`, `gateway/docker-entrypoint.sh`, `tenant-app/Dockerfile`, `tenant-app/docker-entrypoint.sh`

- [x] Add an `internal: true` provisioning network shared only by gateway and provisioner, and remove the gateway's Docker socket mount.
- [x] Add provisioner with the sole socket mount, no published port, a pinned immutable image reference for its own base image where applicable, `no-new-privileges`, dropped capabilities, read-only root filesystem, and a tmpfs for process scratch space.
- [x] Create the fixed application UID/GID in both application images, assign ownership of `/app` and `/app/data`, and make the entrypoints run the application process as that user.
- [x] Apply `user`, `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `read_only: true`, and minimal tmpfs mounts to gateway and provisioned tenant containers. Keep only their data volume writable.

### Task 3: Make the change operable and validate it with Docker

**Files:**
- Modify: `DEPLOY.md`, `.env.example`
- Test: `docker-compose.yml`, gateway and provisioner tests

- [x] Document the one-time ownership migration for existing `gateway_data` and tenant volumes before first redeploy.
- [x] Document that the provisioner is the privileged trust boundary and that Traefik's Docker-provider access is a separately scoped follow-up.
- [x] Run `docker compose config --quiet`, build gateway and provisioner images, and run the unit suites.
- [x] Start only a disposable, temporary Compose project with fake non-secret environment values; verify gateway has no socket mount, provisioner alone has it, and a permitted tenant lifecycle succeeds while a disallowed request is rejected.

### Acceptance criteria

- `docker inspect` shows no Docker socket mount in gateway or tenant containers.
- The only socket mount is in the private provisioner service.
- Gateway and tenant inspect output shows the fixed non-root UID, `NoNewPrivileges=true`, dropped capabilities, and read-only root filesystem.
- Normal tenant provisioning, health polling, stopping, cleanup, and upgrade operations remain functional through the constrained contract.
- Tests cover the allowlist/denylist boundary and all validation commands pass.
