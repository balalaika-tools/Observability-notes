# Docker Compose Resource Identity

Read this file only for Docker Compose or an equivalent one-process-per-
container local deployment. Read `resource_processes.md` as well when a
container runs several telemetry-producing processes.

In the usual one-process-per-container setup, use the container ID as the
source of `service.instance.id` and also record it as `container.id`.

Docker's default container hostname is the container ID, so a checked-in
entrypoint may resolve the final setting before starting the application:

```sh
#!/bin/sh
set -eu
export SERVICE_INSTANCE_ID="${SERVICE_INSTANCE_ID:-${HOSTNAME:?}}"
exec "$@"
```

Do not rely on Compose `${HOSTNAME}` interpolation for this. Compose expands
that expression from the host environment while parsing the file, before the
container exists. Resolve it inside the container, or use a container resource
detector.

If Compose sets a custom `hostname`, `HOSTNAME` is no longer a container ID.
Use a resource detector or the process UUID fallback instead. Do not use the
Compose service name or `container_name`: replicas share or predictably derive
those names, so they are not durable instance identity.

Failure signal: if telemetry records the developer machine hostname, host-side
Compose interpolation ran before container creation.

---

## Then

- back to `resource_identity.md` if any ownership question is still open;
- then continue with `sdk_bootstrap.md`.
