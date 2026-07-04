import os
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


class SandboxConfig(BaseModel):
    """Configuration for the sandbox.

    Attributes:
        remote_runtime_api_url: The hostname for the Remote Runtime API.
        local_runtime_url: The default hostname for the local runtime. You may want to change to http://host.docker.internal for DIND environments
        base_container_image: The base container image from which to build the runtime image.
        runtime_container_image: The runtime container image to use.
        runtime_mode: How to provide the OpenHands runtime. "image" builds or uses a
            full runtime image. "mounted" starts the base image directly and bind-mounts
            a prebuilt runtime bundle.
        runtime_bundle_host_path: Host path to the prebuilt runtime bundle when
            runtime_mode is "mounted".
        runtime_bundle_container_path: Container path where the runtime bundle is mounted.
        runtime_bundle_readonly: Whether the runtime bundle is mounted read-only.
        runtime_executable: Python executable path inside the sandbox for mounted mode.
        runtime_working_dir: Docker working directory used to start the runtime process.
        user_id: The user ID for the sandbox.
        timeout: The timeout for the default sandbox action execution.
        remote_runtime_init_timeout: The timeout for the remote runtime to start.
        remote_runtime_api_timeout: The timeout for the remote runtime API requests.
        remote_runtime_enable_retries: Whether to enable retries (on recoverable errors like requests.ConnectionError) for the remote runtime API requests.
        enable_auto_lint: Whether to enable auto-lint.
        use_host_network: Whether to use the host network.
        runtime_binding_address: The binding address for the runtime ports.  It specifies which network interface on the host machine Docker should bind the runtime ports to.
        initialize_plugins: Whether to initialize plugins.
        force_rebuild_runtime: Whether to force rebuild the runtime image.
        runtime_extra_deps: The extra dependencies to install in the runtime image (typically used for evaluation).
            This will be rendered into the end of the Dockerfile that builds the runtime image.
            It can contain any valid shell commands (e.g., pip install numpy).
            The path to the interpreter is available as $OH_INTERPRETER_PATH,
            which can be used to install dependencies for the OH-specific Python interpreter.
        runtime_startup_env_vars: The environment variables to set at the launch of the runtime.
            This is a dictionary of key-value pairs.
            This is useful for setting environment variables that are needed by the runtime.
            For example, for specifying the base url of website for browsergym evaluation.
        browsergym_eval_env: The BrowserGym environment to use for evaluation.
            Default is None for general purpose browsing. Check evaluation/miniwob and evaluation/webarena for examples.
        enable_mcp: Whether to start the runtime MCP router.
        platform: The platform on which the image should be built. Default is None.
        remote_runtime_resource_factor: Factor to scale the resource allocation for remote runtime.
            Must be one of [1, 2, 4, 8]. Will only be used if the runtime is remote.
        enable_gpu: Whether to enable GPU.
        docker_runtime_kwargs: Additional keyword arguments to pass to the Docker runtime when running containers.
            This should be a JSON string that will be parsed into a dictionary.
        trusted_dirs: List of directories that can be trusted to run the OpenHands CLI.
        vscode_port: The port to use for VSCode. If None, a random port will be chosen.
            This is useful when deploying OpenHands in a remote machine where you need to expose a specific port.
    """

    remote_runtime_api_url: str | None = Field(default='http://localhost:8000')
    local_runtime_url: str = Field(default='http://localhost')
    keep_runtime_alive: bool = Field(default=False)
    pause_closed_runtimes: bool = Field(default=True)
    rm_all_containers: bool = Field(default=False)
    api_key: str | None = Field(default=None)
    base_container_image: str | None = Field(
        default='nikolaik/python-nodejs:python3.12-nodejs22'
    )
    runtime_container_image: str | None = Field(default=None)
    runtime_mode: Literal['image', 'mounted'] = Field(default='image')
    runtime_bundle_host_path: str | None = Field(default=None)
    runtime_bundle_container_path: str = Field(default='/opt/openhands-runtime')
    runtime_bundle_readonly: bool = Field(default=True)
    runtime_executable: str | None = Field(default=None)
    runtime_working_dir: str | None = Field(default=None)
    user_id: int = Field(default=os.getuid() if hasattr(os, 'getuid') else 1000)
    timeout: int = Field(default=120)
    remote_runtime_init_timeout: int = Field(default=180)
    remote_runtime_api_timeout: int = Field(default=10)
    remote_runtime_enable_retries: bool = Field(default=True)
    remote_runtime_class: str | None = Field(
        default=None
    )  # can be "None" (default to gvisor) or "sysbox" (support docker inside runtime + more stable)
    enable_auto_lint: bool = Field(
        default=False
    )  # once enabled, OpenHands would lint files after editing
    use_host_network: bool = Field(default=False)
    runtime_binding_address: str = Field(default='0.0.0.0')
    runtime_extra_build_args: list[str] | None = Field(default=None)
    initialize_plugins: bool = Field(default=True)
    force_rebuild_runtime: bool = Field(default=False)
    runtime_extra_deps: str | None = Field(default=None)
    runtime_startup_env_vars: dict[str, str] = Field(default_factory=dict)
    browsergym_eval_env: str | None = Field(default=None)
    enable_mcp: bool = Field(default=True)
    platform: str | None = Field(default=None)
    close_delay: int = Field(default=15)
    remote_runtime_resource_factor: int = Field(default=1)
    enable_gpu: bool = Field(default=False)
    docker_runtime_kwargs: dict | None = Field(default=None)
    selected_repo: str | None = Field(default=None)
    trusted_dirs: list[str] = Field(default_factory=list)
    vscode_port: int | None = Field(default=None)
    volumes: str | None = Field(
        default=None,
        description="Volume mounts in the format 'host_path:container_path[:mode]', e.g. '/my/host/dir:/workspace:rw'. Multiple mounts can be specified using commas, e.g. '/path1:/workspace/path1,/path2:/workspace/path2:ro'",
    )

    model_config = {'extra': 'forbid'}

    @classmethod
    def from_toml_section(cls, data: dict) -> dict[str, 'SandboxConfig']:
        """
        Create a mapping of SandboxConfig instances from a toml dictionary representing the [sandbox] section.

        The configuration is built from all keys in data.

        Returns:
            dict[str, SandboxConfig]: A mapping where the key "sandbox" corresponds to the [sandbox] configuration
        """
        # Initialize the result mapping
        sandbox_mapping: dict[str, SandboxConfig] = {}

        # Try to create the configuration instance
        try:
            sandbox_mapping['sandbox'] = cls.model_validate(data)
        except ValidationError as e:
            raise ValueError(f'Invalid sandbox configuration: {e}')

        return sandbox_mapping

    @model_validator(mode='after')
    def set_default_base_image(self) -> 'SandboxConfig':
        if self.base_container_image is None:
            self.base_container_image = 'nikolaik/python-nodejs:python3.12-nodejs22'
        return self

    @model_validator(mode='after')
    def validate_mounted_runtime(self) -> 'SandboxConfig':
        if self.runtime_mode != 'mounted':
            return self

        if not self.runtime_bundle_host_path:
            raise ValueError(
                'runtime_bundle_host_path is required when sandbox.runtime_mode is "mounted"'
            )
        if not os.path.isabs(self.runtime_bundle_host_path):
            raise ValueError('runtime_bundle_host_path must be an absolute host path')
        if not os.path.isabs(self.runtime_bundle_container_path):
            raise ValueError(
                'runtime_bundle_container_path must be an absolute container path'
            )
        if self.runtime_executable is not None and not os.path.isabs(
            self.runtime_executable
        ):
            raise ValueError('runtime_executable must be an absolute container path')
        if self.runtime_working_dir is not None and not os.path.isabs(
            self.runtime_working_dir
        ):
            raise ValueError('runtime_working_dir must be an absolute container path')
        if self.runtime_extra_deps:
            raise ValueError(
                'runtime_extra_deps is not supported when sandbox.runtime_mode is "mounted"'
            )
        if self.runtime_extra_build_args:
            raise ValueError(
                'runtime_extra_build_args is not supported when sandbox.runtime_mode is "mounted"'
            )

        runtime_bundle_container_path = self.runtime_bundle_container_path.rstrip('/')
        if not runtime_bundle_container_path:
            runtime_bundle_container_path = '/'
        self.runtime_bundle_container_path = runtime_bundle_container_path

        if self.runtime_executable is None:
            self.runtime_executable = os.path.join(
                self.runtime_bundle_container_path, 'bin', 'python'
            )
        if self.runtime_working_dir is None:
            self.runtime_working_dir = self.runtime_bundle_container_path
        return self
