
import asyncio
import os
import time
import uuid
from copy import deepcopy
from typing import Dict, List, Optional

import aiodocker
import aiohttp
import docker
import requests
import tenacity
from aiodocker.containers import DockerContainer
from aiodocker.types import PortInfo
from fastapi import HTTPException

from openhands.runtime.builder.docker import DockerRuntimeBuilder
from openhands.runtime.utils import find_available_tcp_port
from openhands.utils.async_utils import call_sync_from_async

from ..config import settings
from ..models import RuntimeMount, StartRequest
from ..utils import get_logger
from .builder import BuildManager

logger = get_logger(__name__)
class RuntimeManager:
    def __init__(self):
        # set a large timeout just in case
        # await docker_client = docker.from_env(timeout=300)
        self.active_runtimes: Dict[str, Dict] = {}
        self.active_sessions: Dict[str, str] = {}
        self.build_manager = BuildManager()
        self._allocated_ports_in_flight = set()
        self.sync_docker_client = docker.from_env(timeout=300)
        self._port_lock = asyncio.Lock()
        self._image_builder: DockerRuntimeBuilder | None = None
        self._image_pull_locks: dict[str, asyncio.Lock] = {}

    def _get_image_builder(self) -> DockerRuntimeBuilder:
        if self._image_builder is None:
            self._image_builder = DockerRuntimeBuilder(self.sync_docker_client)
        return self._image_builder

    def _ensure_image_sync(
        self, image_name: str, pull_from_repo: bool
    ) -> dict | None:
        image_exists = self._get_image_builder().image_exists(
            image_name, pull_from_repo
        )
        if not image_exists:
            return None

        image = self.sync_docker_client.images.get(image_name)
        return {
            'upload_time': image.attrs['Created'],
            'image_size_bytes': image.attrs['Size'],
        }

    async def ensure_image(
        self, image_name: str, pull_from_repo: bool = True
    ) -> dict | None:
        lock = self._image_pull_locks.setdefault(image_name, asyncio.Lock())
        async with lock:
            return await call_sync_from_async(
                self._ensure_image_sync, image_name, pull_from_repo
            )

    async def _get_ports_in_use_docker(self, docker_client: aiodocker.Docker) -> set:
        containers = await docker_client.containers.list()
        ports = set()
        for container in containers:
            port_bindings = container._container.get("HostConfig", {}).get("PortBindings", {})
            for _, host_maps in port_bindings.items():
                if host_maps:
                    host_ports = [int(host_map["HostPort"]) for host_map in host_maps]
                    for port in host_ports:
                        ports.add(port)
        return ports

    async def release_ports(self, ports):
        async with self._port_lock:
            for port in ports:
                self._allocated_ports_in_flight.discard(port)

    def _sync_get_ports_in_use(self):
        ret = deepcopy(self._allocated_ports_in_flight)
        containers = self.sync_docker_client.containers.list()
        for container in containers:
            for _, host_maps in container.ports.items():
                if host_maps:
                    host_ports = [host_map["HostPort"] for host_map in host_maps]
                    for port_str in host_ports:
                        ret.add(int(port_str))
        return ret

    def _sync_find_available_port(self, port_range, ports_in_use, max_attempts=5):
        port = port_range[1]
        for _ in range(max_attempts):
            port = find_available_tcp_port(port_range[0], port_range[1])
            if port not in ports_in_use:
                return port
        # If no port is found after max_attempts, return the last tried port
        logger.info("Max attempts reached for port finding")
        return port

    async def _get_ports(self, docker_client: aiodocker.Docker, session_id: str):
        s = time.time()
        async with self._port_lock:
            ports_in_use = await self._get_ports_in_use_docker(docker_client)
            for port in self._allocated_ports_in_flight:
                ports_in_use.add(port)
            def _find_ports_task():
                container_port = self._sync_find_available_port(
                        settings.EXECUTION_SERVER_PORT_RANGE,
                        ports_in_use=ports_in_use
                    )
                vscode_port = self._sync_find_available_port(settings.VSCODE_PORT_RANGE,
                                                            ports_in_use=ports_in_use)
                app_ports = [
                    self._sync_find_available_port(settings.APP_PORT_RANGE_1, ports_in_use=ports_in_use),
                    self._sync_find_available_port(settings.APP_PORT_RANGE_2, ports_in_use=ports_in_use),
                ]
                return container_port, vscode_port, app_ports
            # Run in a different thread so that we don't block the event loop
            # At any point in time, the number of such threads running is 1 because we use a lock
            container_port, vscode_port, app_ports = await call_sync_from_async(_find_ports_task)
            for port in [container_port, vscode_port, *app_ports]:
                self._allocated_ports_in_flight.add(port)
        e = time.time()
        logger.info(f"Port finding took {e-s} seconds for session {session_id}")
        return container_port, vscode_port, app_ports

    def _is_allowed_runtime_mount_path(self, host_path: str) -> bool:
        allowed_prefixes = settings.get_remote_runtime_allowed_mount_prefixes()
        if not allowed_prefixes:
            return False

        real_host_path = os.path.realpath(host_path)
        for prefix in allowed_prefixes:
            real_prefix = os.path.realpath(prefix)
            if real_host_path == real_prefix or real_host_path.startswith(
                real_prefix + os.sep
            ):
                return True
        return False

    def _validate_runtime_bundle(
        self, host_path: str, container_path: str
    ) -> None:
        if not os.path.isdir(host_path):
            raise HTTPException(
                status_code=400,
                detail=f'Runtime bundle host path is not a directory: {host_path}',
            )

        required_executables = (
            'bin/python',
            'env/bin/python',
            'tools/bin/search',
            'tools/bin/str_replace_editor',
        )
        invalid_executables = [
            relative_path
            for relative_path in required_executables
            if not os.path.isfile(os.path.join(host_path, relative_path))
            or not os.access(os.path.join(host_path, relative_path), os.X_OK)
        ]
        if invalid_executables:
            raise HTTPException(
                status_code=400,
                detail=(
                    'Runtime bundle is missing required executable files: '
                    + ', '.join(invalid_executables)
                ),
            )

        metadata_path = os.path.join(host_path, 'meta/container_path')
        try:
            with open(metadata_path, encoding='utf-8') as metadata_file:
                bundle_container_path = metadata_file.read().strip()
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=f'Runtime bundle metadata is unreadable: {metadata_path}: {exc}',
            ) from exc

        if bundle_container_path != container_path:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'Runtime bundle was built for container path '
                    f'{bundle_container_path}, not {container_path}'
                ),
            )

    def _get_runtime_mount_binds(self, runtime_mounts: List[RuntimeMount]) -> List[str]:
        if not runtime_mounts:
            return []

        if not settings.get_remote_runtime_allowed_mount_prefixes():
            raise HTTPException(
                status_code=400,
                detail='Runtime mounts are disabled on this remote runtime server.',
            )

        binds = []
        for mount in runtime_mounts:
            host_path = os.path.realpath(mount.host_path)
            mode = mount.mode
            if mode != 'ro' and not settings.REMOTE_RUNTIME_ALLOW_RW_MOUNTS:
                raise HTTPException(
                    status_code=403,
                    detail='Read-write runtime mounts are disabled on this remote runtime server.',
                )
            if not os.path.exists(host_path):
                raise HTTPException(
                    status_code=400,
                    detail=f'Runtime mount host path does not exist: {mount.host_path}',
                )
            if not self._is_allowed_runtime_mount_path(host_path):
                raise HTTPException(
                    status_code=403,
                    detail=f'Runtime mount host path is not allowed: {mount.host_path}',
                )
            self._validate_runtime_bundle(host_path, mount.container_path)
            binds.append(f'{host_path}:{mount.container_path}:{mode}')
        return binds


    async def start_runtime(self, docker_client: aiodocker.Docker, request: StartRequest) -> Dict:
        binds = self._get_runtime_mount_binds(request.runtime_mounts)
        runtime_id = str(uuid.uuid4())
        container_name = f'{settings.CONTAINER_NAME_PREFIX}{request.session_id}'
        resource_factor = request.resource_factor
        ports = None
        try:
            # Only one `start_runtime` task can find ports at a time
            container_port, vscode_port, app_ports = await self._get_ports(docker_client, request.session_id)
            ports = [container_port, vscode_port, app_ports[0], app_ports[1]]

            network_mode = 'host' if settings.USE_HOST_NETWORK else None

            port_mapping = None
            if not settings.USE_HOST_NETWORK:
                port_mapping = {
                    f'{container_port}/tcp': [{'HostPort': str(container_port)}],
                    f'{vscode_port}/tcp': [{'HostPort': str(vscode_port)}],
                }

                for port in app_ports:
                    port_mapping[f'{port}/tcp'] = [{'HostPort': str(port)}]


            device_requests = []
            if request.enable_gpu:
                device_requests.append(
                    {
                        "Driver": "",
                        "Count": -1,
                        "DeviceIDs": None,
                        "Capabilities": [["gpu"]],
                        "Options": {}
                    }
                )
            docker_runtime_kwargs = settings.get_docker_runtime_kwargs(resource_factor)
            # API host config
            host_config = {
                    'NetworkMode': network_mode,
                    "PortBindings": port_mapping,
                    "DeviceRequests": device_requests,
                    **docker_runtime_kwargs,
            }
            # API doens't take `None` values
            host_config = {k: v for k, v in host_config.items() if v is not None}

            volumes = None
            for mount in request.runtime_mounts:
                if volumes is None:
                    volumes = {}
                volumes[mount.container_path] = {}

            if request.workspace_mount_path and request.workspace_mount_path_in_sandbox:
                # API format
                if volumes is None:
                    volumes = {}
                volumes[request.workspace_mount_path_in_sandbox] = {}
                # For Binds in HostConfig
                binds.append(f"{request.workspace_mount_path}:{request.workspace_mount_path_in_sandbox}:rw")

            if binds:
                host_config["Binds"] = binds

            environment = {
                'port': str(container_port),
                'PYTHONUNBUFFERED': '1',
                'VSCODE_PORT': str(vscode_port),
                **request.environment,
            }
            # Rewrite environment in the Docker API format
            environment = [f"{k}={v}" for k,v in environment.items()]

            # replace port used in command:
            for i, arg in enumerate(request.command):
                if arg.isdigit() and int(arg) == 88888888:
                    request.command[i] = str(container_port)


            container_config = {
                'Image': request.image,
                'Cmd': request.command,
                'WorkingDir': request.working_dir,
                'Env': environment,
                "ExposedPorts": {k: {} for k in port_mapping.keys()},
                # maybe containers are alwaysin detach mode?
                # 'Detach': True,
                'Volumes': volumes,
                "HostConfig": host_config
            }
            print(container_config)

            # API doesn't take None values
            container_config = {k: v for k, v in container_config.items() if v is not None}

            container = await docker_client.containers.run(config=container_config, name=container_name)

            # NOTE: we keep runtime-info in the older docker python SDK format to be compatible with the OH client
            runtime_info = {
                'container_id': container.id,
                'session_id': request.session_id,
                'status': 'running',
                'restart_count': 0,
                'restart_reasons': [],
                'ports': {
                    'container_port': container_port,
                    'vscode_port': vscode_port,
                    'app_ports': app_ports,
                },
                'workspace': {
                    'mount_path': request.workspace_mount_path,
                    'mount_path_in_sandbox': request.workspace_mount_path_in_sandbox,
                }
                if request.workspace_mount_path
                else None,
                'use_host_network': request.use_host_network,
                'gpu_enabled': request.enable_gpu,
            }

            self.active_runtimes[runtime_id] = runtime_info
            self.active_sessions[request.session_id] = runtime_id

            await self._wait_until_alive(container, runtime_id)

            host = settings.PUBLIC_HOST
            return {
                'runtime_id': runtime_id,
                'url': f'http://{host}:{container_port}',
                'work_hosts': {f'http://{host}:{port}': port for port in app_ports},
                'session_api_key': str(uuid.uuid4()),
            }

        except HTTPException as e:
            logger.error(f'Failed to start runtime: {str(e)}')
            if runtime_id in self.active_runtimes:
                await self.stop_runtime(docker_client, runtime_id, remove=True)
            raise
        except Exception as e:
            logger.error(f'Failed to start runtime: {str(e)}')
            # for port in ([container_port, vscode_port] + app_ports):
            #     await self.release_port(port)
            if runtime_id in self.active_runtimes:
                await self.stop_runtime(docker_client, runtime_id, remove=True)
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if ports:
                await self.release_ports(ports)

    @tenacity.retry(
        stop=tenacity.stop_after_delay(120),
        retry=tenacity.retry_if_exception_type(
            (ConnectionError, requests.exceptions.ConnectionError)
        ),
        wait=tenacity.wait_fixed(2),
    )
    async def _wait_until_alive(self, container: DockerContainer, runtime_id: str):
        # print('#' * 30)
        status = await self._get_container_status(container)
        if status == "exited":
            raise HTTPException(
                status_code=503, detail=f'Container {runtime_id} has exited.'
            )

        runtime_info = self.active_runtimes[runtime_id]
        port = runtime_info['ports']['container_port']
        host = settings.RUNTIME_HEALTHCHECK_HOST
        url = f'http://{host}:{port}/alive'

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=2) as response:
                    if response.status != 200:
                        raise ConnectionError(f'Container not ready at {url}')
        except Exception as e:
            raise ConnectionError(f'Container not responding at {url}: {e}')

    async def stop_runtime(self, docker_client: aiodocker.Docker, runtime_id: str, remove: bool = True):
        """Stop and optionally remove a runtime container."""
        if runtime_id not in self.active_runtimes:
            raise HTTPException(status_code=404, detail='Runtime not found')

        try:
            runtime_info = self.active_runtimes[runtime_id]
            container = await docker_client.containers.get(runtime_info['container_id'])

            # Stop the container
            await container.stop()

            if remove:
                await container.delete()
                # Clean up runtime records
                session_id = runtime_info['session_id']
                del self.active_runtimes[runtime_id]
                del self.active_sessions[session_id]
            else:
                runtime_info['status'] = 'stopped'

        except (docker.errors.NotFound, aiodocker.exceptions.DockerError):
            # Container already removed, clean up records
            session_id = self.active_runtimes[runtime_id]['session_id']
            del self.active_runtimes[runtime_id]
            del self.active_sessions[session_id]
        except Exception as e:
            logger.error(f'Failed to stop runtime: {str(e)}')
            raise HTTPException(
                status_code=500, detail=f'Failed to stop runtime: {str(e)}'
            )

    async def pause_runtime(self, docker_client: aiodocker.Docker, runtime_id: str):
        """Pause a running runtime container."""
        if runtime_id not in self.active_runtimes:
            raise HTTPException(status_code=404, detail='Runtime not found')

        try:
            runtime_info = self.active_runtimes[runtime_id]
            container = await docker_client.containers.get(runtime_info['container_id'])

            # First, ensure environment variables are properly persisted
            # This matches the local implementation's behavior
            try:
                await self._persist_environment(container)
            except Exception as e:
                logger.warning(f'Failed to persist environment variables: {str(e)}')

            # Stop the container but don't remove it
            await container.stop()
            runtime_info['status'] = 'paused'

        except docker.errors.NotFound:
            raise HTTPException(
                status_code=404, detail='Container not found. It may have been removed.'
            )
        except Exception as e:
            logger.error(f'Failed to pause runtime: {str(e)}')
            raise HTTPException(
                status_code=500, detail=f'Failed to pause runtime: {str(e)}'
            )

    async def resume_runtime(self, docker_client: aiodocker.Docker, runtime_id: str):
        """Resume a paused runtime container."""
        if runtime_id not in self.active_runtimes:
            raise HTTPException(status_code=404, detail='Runtime not found')

        try:
            runtime_info = self.active_runtimes[runtime_id]
            container = await docker_client.containers.get(runtime_info['container_id'])

            # Start the container
            await container.start()
            runtime_info['status'] = 'running'

            # Wait for container to be ready
            await self._wait_until_alive(container, runtime_id)

        except docker.errors.NotFound:
            raise HTTPException(
                status_code=404, detail='Container not found. It may have been removed.'
            )
        except Exception as e:
            logger.error(f'Failed to resume runtime: {str(e)}')
            raise HTTPException(
                status_code=500, detail=f'Failed to resume runtime: {str(e)}'
            )

    async def get_runtime_status(self, docker_client: aiodocker.Docker, runtime_id: str) -> Dict:
        """Get detailed runtime status information."""
        if runtime_id not in self.active_runtimes:
            return {'runtime_id': runtime_id, 'pod_status': 'not found'}

        try:
            runtime_info = self.active_runtimes[runtime_id]
            container: DockerContainer = await docker_client.containers.get(runtime_info['container_id'])
            # container.reload()  # Refresh container information

            # Get container stats
            stats_list = await container.stats(stream=False)
            stats = stats_list[-1]

            # Calculate memory usage
            memory_stats = stats.get('memory_stats', {})
            memory_usage = memory_stats.get('usage', 0)
            memory_limit = memory_stats.get('limit', 1)
            memory_percent = (memory_usage / memory_limit) * 100

            # Calculate CPU usage
            cpu_stats = stats.get('cpu_stats', {})
            precpu_stats = stats.get('precpu_stats', {})
            cpu_delta = cpu_stats.get('cpu_usage', {}).get(
                'total_usage', 0
            ) - precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
            system_delta = cpu_stats.get('system_cpu_usage', 0) - precpu_stats.get(
                'system_cpu_usage', 0
            )
            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta > 0:
                cpu_percent = (
                    (cpu_delta / system_delta) * 100.0 * cpu_stats.get('online_cpus', 1)
                )

            status_info = {
                'runtime_id': runtime_id,
                'pod_status': await self._get_container_status(container),
                'restart_count': runtime_info['restart_count'],
                'restart_reasons': runtime_info['restart_reasons'],
                'session_id': runtime_info['session_id'],
                'ports': runtime_info['ports'],
                'resources': {
                    'memory_usage_bytes': memory_usage,
                    'memory_limit_bytes': memory_limit,
                    'memory_percent': round(memory_percent, 2),
                    'cpu_percent': round(cpu_percent, 2),
                },
                'network': {
                    'networks': container['NetworkSettings']['Networks'],
                    'ports': container['NetworkSettings']['Ports'],
                },
                'created_at': container['Created'],
                'started_at': container['State']['StartedAt'],
            }

            # Add GPU information if enabled
            if runtime_info.get('gpu_enabled'):
                gpu_info = await self._get_gpu_info(container)
                if gpu_info:
                    status_info['resources']['gpu'] = gpu_info

            # Add health check information
            status_info['health_check'] = await self._check_container_health(
                container, runtime_info
            )

            return status_info

        except docker.errors.NotFound:
            return {'runtime_id': runtime_id, 'pod_status': 'not found'}
        except Exception as e:
            logger.error(f'Failed to get runtime status: {str(e)}')
            raise HTTPException(
                status_code=500, detail=f'Failed to get runtime status: {str(e)}'
            )

    async def _persist_environment(self, container: DockerContainer) -> None:
        """Persist environment variables to container's .bashrc file."""
        try:
            # Check if .bashrc exists and create it if it doesn't
            await container.exec('touch /root/.bashrc')

            # Get current environment variables
            env_result = await container.exec('env')
            if env_result.exit_code == 0:
                env_vars = env_result.output.decode().strip().split('\n')

                # Prepare environment variable export commands
                export_commands = []
                for env_var in env_vars:
                    if '=' in env_var:
                        key, value = env_var.split('=', 1)
                        # Escape special characters in the value
                        value = value.replace('"', '\\"')
                        export_commands.append(f'export {key}="{value}"')

                # Write to .bashrc
                bashrc_content = '\n'.join(export_commands)
                await container.exec(
                    f'bash -c \'echo "{bashrc_content}" > /root/.bashrc\''
                )
        except Exception as e:
            logger.warning(f'Failed to persist environment variables: {str(e)}')
            raise

    async def _get_gpu_info(self, container: DockerContainer) -> Optional[Dict]:
        """Get GPU information from container if available."""
        try:
            exec_result = await container.exec(
                'nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits'
            )
            if exec_result.exit_code == 0:
                gpu_stats = exec_result.output.decode().strip().split(',')
                return {
                    'utilization_percent': float(gpu_stats[0]),
                    'memory_used_mb': float(gpu_stats[1]),
                    'memory_total_mb': float(gpu_stats[2]),
                }
        except Exception as e:
            logger.warning(f'Failed to get GPU info: {str(e)}')
        return None

    async def _check_container_health(self, container: DockerContainer, runtime_info: Dict) -> Dict:
        """Check container health status including API endpoint responses."""
        health_info = {
            'container_status': await self._get_container_status(container),
            'api_responsive': False,
            'vscode_responsive': False
            if runtime_info['ports'].get('vscode_port')
            else None,
            'app_ports_responsive': {},
        }

        # Check main API endpoint
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://{settings.RUNTIME_HEALTHCHECK_HOST}:{runtime_info['ports']['container_port']}/alive"
                async with session.get(url, timeout=2) as response:
                    health_info['api_responsive'] = response.status == 200
        except:
            pass

        # Check VSCode endpoint if enabled
        if runtime_info['ports'].get('vscode_port'):
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"http://{settings.RUNTIME_HEALTHCHECK_HOST}:{runtime_info['ports']['vscode_port']}"
                    async with session.get(url, timeout=2) as response:
                        health_info['vscode_responsive'] = response.status == 200
            except:
                pass

        # Check app ports
        for port in runtime_info['ports'].get('app_ports', []):
            try:
                async with aiohttp.ClientSession() as session:
                    url = f'http://{settings.RUNTIME_HEALTHCHECK_HOST}:{port}'
                    async with session.get(url, timeout=2) as response:
                        health_info['app_ports_responsive'][port] = (
                            response.status == 200
                        )
            except:
                health_info['app_ports_responsive'][port] = False

        return health_info

    @staticmethod
    async def _get_container_status(container: DockerContainer):
        container_info = await container.show()
        return container_info.get("State", {}).get("Status", "exited")
