# SkyRL-OpenHands: OpenHands With A Scalable Remote Runtime Server

This repository is a fork of [OpenHands](https://github.com/All-Hands-AI/OpenHands), with the remote runtime server implementation used in [SkyRL-v0](https://novasky-ai.notion.site/skyrl-v0). We provide:

1. An efficient remote server implementation for OpenHands: We leverage [aiodocker](https://aiodocker.readthedocs.io/en/latest/) and use the [crun](https://github.com/containers/crun) container runtime, which offers lightweight and high-performance container execution. Our original overcommitted deployment ran 80–100 containers per replica on 16-CPU nodes; actual safe concurrency depends on workload memory and should be established with load testing.
2. Deployment files for a scalable deployment on Kubernetes: We leverage storage-optimized instances to cache container images and enable fast startup times. Further, we use a simple API gateway to spread the image cache across multiple replicas. 

While there remains significant room for optimization, our current implementation is simple, effective, and publicly available.


## Main Changes for R2E Rollouts

- Tool calling: prefer the Qwen/OpenAI-compatible text tool-call parser introduced in commit `89a9852f3b1ada341651012804fc0f10e64ef4d0` (`Add Qwen tool parsing and R2E eval support`). This parser accepts `<tool_call>{...}</tool_call>` responses and converts them into OpenAI-style `tool_calls` before CodeAct maps them to OpenHands actions.

- Dependency management: use `uv` and `uv.lock` instead of Poetry and `poetry.lock`. Create the local environment with `uv venv --python 3.12 .venv`, install with `uv sync`, and run commands from the local `.venv`.

- Runtime provisioning: use mounted runtime mode for R2E rollouts instead of rebuilding an OpenHands runtime image for every environment. Start from the R2E dataset image, mount a prebuilt OpenHands runtime bundle/executable into the sandbox with `--runtime-mode mounted`, `--runtime-bundle-host-path`, and `--runtime-bundle-container-path`, then run the action execution server from that mounted bundle. This keeps BIG doing the heavy runtime work while avoiding repeated dependency installation inside each R2E image.

The older mocked `<function=...><parameter=...>` path and image-based runtime path still exist as fallbacks, but new rollout prompts and compatibility testing should target the commit `89a9852f3b1ada341651012804fc0f10e64ef4d0` parser and mounted runtime path.


## Quick Start: Standalone Remote Runtime

The intended deployment is one Linux server with 32 CPU cores and 128 GB of
memory, using the remote runtime in mounted mode without Kubernetes. Start with
24–28 evaluation workers and increase only after measuring host memory.

Set the server address and a shared API key, then install the project:

```bash
export SERVER_IP="server-ip-or-hostname"
export API_KEY="replace-with-a-secret"
uv venv --python 3.12 .venv
uv sync
```

Install `crun`, configure it as Docker's `default-runtime` in
`/etc/docker/daemon.json`, restart Docker when no rollouts are active, and verify
that `docker info --format '{{.DefaultRuntime}}'` prints `crun`.

Allow inbound TCP ports `3000` and `30000–59999` from the evaluation client.
Port `3000` serves the remote runtime API; the remaining ports are assigned to
runtime containers and their application endpoints.

Build the mounted runtime bundle and launch the server:

```bash
sudo install -d -o "$(id -u)" -g "$(id -g)" /opt/openhands-runtime
./containers/runtime/build_mounted_runtime_bundle.sh \
  /opt/openhands-runtime/current /opt/openhands-runtime

OPENHANDS_API_KEY="${API_KEY}" \
REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES=/opt/openhands-runtime \
.venv/bin/python -m openhands.runtime.remote_runtime_server.main \
  --host "${SERVER_IP}" --port 3000
```

Run evaluations with the same API key:

```bash
ALLHANDS_API_KEY="${API_KEY}" \
RUNTIME=remote \
SANDBOX_REMOTE_RUNTIME_API_URL="http://${SERVER_IP}:3000" \
SANDBOX_RUNTIME_MODE=mounted \
SANDBOX_RUNTIME_BUNDLE_HOST_PATH=/opt/openhands-runtime/current \
./evaluation/benchmarks/swe_bench/scripts/run_infer.sh \
  llm.gpt4o-mini HEAD CodeActAgent 1 10 24 \
  "princeton-nlp/SWE-bench_Lite" test
```

`SERVER_IP` must be reachable from the evaluation client. The bundle host path
is resolved on the remote runtime server, not the evaluation client.


## Remote Runtime Server

The remote runtime server implementation is provided in [openhands/runtime/remote_runtime_server](./openhands/runtime/remote_runtime_server/README.md).


## Deployment

For deployment instructions, please refer to the [k8s_deploy](./k8s_deploy/README.md) directory.
