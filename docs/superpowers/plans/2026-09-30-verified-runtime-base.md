# Reuse an exact verified tenant runtime base

The production host has about 801 MiB free after removing unused build cache and an owned image never deployed. Retained rollback containers and encrypted backups are preserved. The current dependency/runtime inputs are unchanged from deployed ef616ed; rebuilding the same libraries needs avoidable temporary disk space.

1. Require the immutable base image ID and its exact ef616ed OCI revision, not a mutable tag. Verify dependency and runtime configuration files against the base Git commit; verify actual image bytes for files present inside it. Block deleted runtime source files to prevent overlay leftovers.
2. Derive the new Dockerfile from the unchanged original frontend stage and complete original runtime COPY/revision/nginx/health/entrypoint tail. It inherits only the already tested runtime libraries/environment from the exact base. No requirements, apt, nginx or entrypoint changes are permitted through this path. The normal full Dockerfile remains available.
3. Independently review the file, gate and manifests. Verify committed source and exact base on the host before build. Record the build method explicitly; it is not a new full dependency installation.
4. Run unchanged exact-image CSV, scheduler/PDF/crash, composed business, shared warehouse, encrypted restore/candidate/rollback and browser gates. Deploy only after they pass, then verify the live revision, assets and APIs and update the provisioner default.

This optimization does not permit deleting customer data, backups or old rollback images, weakening isolation, changing dependencies, or skipping acceptance.
