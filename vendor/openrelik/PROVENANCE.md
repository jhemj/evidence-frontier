# OpenRelik upstream references

- deploy source: https://github.com/openrelik/openrelik-deploy/tree/f44ed1f191945d77ab8d8d2d4704db95856e0edd
- server source: https://github.com/openrelik/openrelik-server/tree/49e40a4b90245579fe76aa0b280acfe968ba518a
- `compose.upstream.yaml`: unmodified `docker/docker-compose_0.7.0.yml`.
- `config.upstream.env`: unmodified `docker/config_0.7.0.env`.
- `prometheus.yml`: unmodified `docker/prometheus.yml`.
- `settings.upstream.toml`: unmodified server `settings_example.toml` from the inspected commit. Validate settings against the selected release before production.
- License: Apache-2.0, included in LICENSE. Copyright 2024–2026 Google LLC.

The preparation script creates a **modified downstream deployment**, removes optional monitoring, uses Linux named volumes for PostgreSQL on Windows and Ubuntu, and assigns a separate Compose project name. No upstream server or UI source is modified. Runtime secrets are generated locally and excluded from Git.

Release tags are recorded by upstream. Before case use, run `scripts/pin_images.py` on the prepared Compose file to resolve every image to a registry digest and retain that override with the deployment record. The workbench's native controller/worker are independently built images.
