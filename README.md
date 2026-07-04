# SkyRL-OpenHands: OpenHands With A Scalable Remote Runtime Server

Notes (Jianhong) 6/5:
1. I need to understand the best SFT recipe before launching large scale rollout collection. Does the agent obesrve past reasoning content? Should we create a turn-based dataset and train on the final tool call, or should we train on the whole trajectory while masking all observations and training on all tool calls simultaneously? Should we allow and train across compaction? What tools do we expose for the agents?

This repository is a fork of [OpenHands](https://github.com/All-Hands-AI/OpenHands), with the remote runtime server implementation used in [SkyRL-v0](https://novasky-ai.notion.site/skyrl-v0). We provide:
1. An efficient remote server implementation for OpenHands: We leverage [aidocker](https://aiodocker.readthedocs.io/en/latest/) and use the [crun](https://github.com/containers/crun) container runtime, which offers lightweight and high-performance container execution. Our setup supports easily running 80–100 containers per replica on nodes with just 16 CPUs.
2. Deployment files for a scalable deployment on Kubernetes: We leverage storage-optimized instances to cache container images and enable fast startup times. Further, we use a simple API gateway to spread the image cache across multiple replicas. 

While there remains significant room for optimization, our current implementation is simple, effective, and publicly available.


## Main Changes for R2E Rollouts

- Tool calling: prefer the Qwen/OpenAI-compatible text tool-call parser introduced in commit `89a9852f3b1ada341651012804fc0f10e64ef4d0` (`Add Qwen tool parsing and R2E eval support`). This parser accepts `<tool_call>{...}</tool_call>` responses and converts them into OpenAI-style `tool_calls` before CodeAct maps them to OpenHands actions.

- Dependency management: use `uv` and `uv.lock` instead of Poetry and `poetry.lock`. Create the local environment with `uv venv --python 3.12 .venv`, install with `uv sync`, and run commands from the local `.venv`.

- Runtime provisioning: use mounted runtime mode for R2E rollouts instead of rebuilding an OpenHands runtime image for every environment. Start from the R2E dataset image, mount a prebuilt OpenHands runtime bundle/executable into the sandbox with `--runtime-mode mounted`, `--runtime-bundle-host-path`, and `--runtime-bundle-container-path`, then run the action execution server from that mounted bundle. This keeps BIG doing the heavy runtime work while avoiding repeated dependency installation inside each R2E image.

The older mocked `<function=...><parameter=...>` path and image-based runtime path still exist as fallbacks, but new rollout prompts and compatibility testing should target the commit `89a9852f3b1ada341651012804fc0f10e64ef4d0` parser and mounted runtime path.


## Remote Runtime Server

The remote runtime server implementation is provided in [openhands/runtime/remote_runtime_server](./openhands/runtime/remote_runtime_server/README.md).


## Deployment

For deployment instructions, please refer to the [k8s_deploy](./k8s_deploy/README.md) directory.
