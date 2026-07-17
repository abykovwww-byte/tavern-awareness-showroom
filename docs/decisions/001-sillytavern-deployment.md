# 001: SillyTavern Deployment

## Status

Accepted for iteration 1.

## Context

The task requires an official SillyTavern deployment, persistent data, no secrets in Git, LAN-only access, and a configurable NVIDIA OpenAI-compatible backend.

Official references checked:

- https://docs.sillytavern.app/installation/docker/
- https://docs.sillytavern.app/usage/remoteconnections/
- https://docs.sillytavern.app/usage/api-connections/openai/
- https://docs.api.nvidia.com/nim/reference/z-ai-glm-5.2

## Decision

Use the official GHCR image pinned to `ghcr.io/sillytavern/sillytavern:1.18.0`.

Deploy via the existing GitHub/Ansible pull model:

- source of truth: GitHub repository `abykovwww-byte/ubuntu_ansible_palybooks`;
- server checkout: `/opt/ubuntu_ansible_palybooks`;
- app project dir: `/srv/apps/rp-stack`;
- persistent data dir: `/srv/app-data/rp-stack`;
- backup dir: `/srv/backups/rp-stack`.

Bind the service to `192.168.1.88:8000`, not `0.0.0.0`.

Enable SillyTavern whitelist and Basic Auth. Generate the Basic Auth password on the server with Ansible `password` lookup and store it outside Git under `/etc/ansible`.

The NVIDIA API key is entered manually in SillyTavern UI for iteration 1.

## Consequences

- The service is reproducible through IaC and GitHub.
- Secrets are not committed.
- Direct internet exposure is avoided.
- Iteration 1 still depends on manual NVIDIA API configuration in the UI.

