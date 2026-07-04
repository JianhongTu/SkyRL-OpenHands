import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from evaluation.benchmarks.swe_bench.run_infer import instance_to_json_safe_dict
from openhands.agenthub.codeact_agent.codeact_agent import CodeActAgent
from openhands.core.config.agent_config import AgentConfig
from openhands.core.config.app_config import AppConfig
from openhands.core.config.sandbox_config import SandboxConfig
from openhands.runtime.remote_runtime_server.config import (
    Settings,
    resolve_public_host,
    settings,
)
from openhands.runtime.remote_runtime_server.managers.runtime import RuntimeManager
from openhands.runtime.remote_runtime_server.models import RuntimeMount, StartRequest
from openhands.runtime.utils.command import (
    DEFAULT_PYTHON_PREFIX,
    get_action_execution_server_startup_command,
)


def test_mounted_runtime_config_sets_container_defaults():
    sandbox = SandboxConfig(
        runtime_mode='mounted',
        runtime_bundle_host_path='/opt/openhands-runtime/current',
    )

    assert sandbox.runtime_bundle_container_path == '/opt/openhands-runtime'
    assert sandbox.runtime_executable == '/opt/openhands-runtime/bin/python'
    assert sandbox.runtime_working_dir == '/opt/openhands-runtime'
    assert sandbox.runtime_bundle_readonly is True


def test_r2e_instance_serialization_converts_pandas_arrays():
    instance = pd.Series(
        {
            'instance_id': 'namanjain12/example:abc123',
            'modified_files': pd.array(['a.py', 'b.py']),
            'nested': {'values': pd.array([1, 2])},
            'missing': pd.NA,
        }
    )

    safe = instance_to_json_safe_dict(instance)

    assert safe['modified_files'] == ['a.py', 'b.py']
    assert safe['nested']['values'] == [1, 2]
    assert safe['missing'] is None
    json.dumps(safe)


def test_mounted_runtime_config_requires_absolute_host_path():
    with pytest.raises(ValidationError):
        SandboxConfig(
            runtime_mode='mounted',
            runtime_bundle_host_path='openhands-runtime/current',
        )


def test_mounted_runtime_config_rejects_build_only_options():
    with pytest.raises(ValidationError):
        SandboxConfig(
            runtime_mode='mounted',
            runtime_bundle_host_path='/opt/openhands-runtime/current',
            runtime_extra_deps='pip install numpy',
        )


def test_default_runtime_command_uses_image_prefix():
    app_config = AppConfig()

    command = get_action_execution_server_startup_command(
        server_port=30000,
        plugins=[],
        app_config=app_config,
    )

    assert command[: len(DEFAULT_PYTHON_PREFIX) + 1] == [
        *DEFAULT_PYTHON_PREFIX,
        'python',
    ]


def test_mounted_runtime_command_uses_direct_executable():
    sandbox = SandboxConfig(
        runtime_mode='mounted',
        runtime_bundle_host_path='/opt/openhands-runtime/current',
    )
    app_config = AppConfig(sandbox=sandbox)

    command = get_action_execution_server_startup_command(
        server_port=30000,
        plugins=[],
        app_config=app_config,
        python_prefix=[],
        python_executable=sandbox.runtime_executable or '',
    )

    assert command[:4] == [
        '/opt/openhands-runtime/bin/python',
        '-u',
        '-m',
        'openhands.runtime.action_execution_server',
    ]


def test_runtime_command_can_disable_mcp():
    app_config = AppConfig(sandbox=SandboxConfig(enable_mcp=False))

    command = get_action_execution_server_startup_command(
        server_port=30000,
        plugins=[],
        app_config=app_config,
    )

    assert '--disable-mcp' in command


def test_start_request_accepts_runtime_mounts():
    request = StartRequest(
        image='ubuntu:22.04',
        command=['/opt/openhands-runtime/bin/python'],
        working_dir='/opt/openhands-runtime',
        environment={},
        session_id='test-session',
        resource_factor=1,
        runtime_mounts=[
            {
                'host_path': '/opt/openhands-runtime/current',
                'container_path': '/opt/openhands-runtime',
                'mode': 'ro',
            }
        ],
    )

    assert request.runtime_mounts[0].host_path == '/opt/openhands-runtime/current'
    assert request.runtime_mounts[0].container_path == '/opt/openhands-runtime'
    assert request.runtime_mounts[0].mode == 'ro'


def test_start_request_rejects_relative_runtime_mounts():
    with pytest.raises(ValidationError):
        StartRequest(
            image='ubuntu:22.04',
            command=['/opt/openhands-runtime/bin/python'],
            working_dir='/opt/openhands-runtime',
            environment={},
            session_id='test-session',
            resource_factor=1,
            runtime_mounts=[
                {
                    'host_path': 'openhands-runtime/current',
                    'container_path': '/opt/openhands-runtime',
                    'mode': 'ro',
                }
            ],
        )


def test_remote_runtime_mounts_reject_rw_by_default(tmp_path, monkeypatch):
    manager = RuntimeManager.__new__(RuntimeManager)
    monkeypatch.setattr(
        settings, 'REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES', [str(tmp_path)]
    )
    monkeypatch.setattr(settings, 'REMOTE_RUNTIME_ALLOW_RW_MOUNTS', False)

    with pytest.raises(HTTPException) as exc_info:
        manager._get_runtime_mount_binds(
            [
                RuntimeMount(
                    host_path=str(tmp_path),
                    container_path='/opt/openhands-runtime',
                    mode='rw',
                )
            ]
        )

    assert getattr(exc_info.value, 'status_code', None) == 403


def test_remote_runtime_allowed_mount_prefixes_accept_single_path():
    config = Settings(REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES='/opt/openhands-runtime')

    assert config.get_remote_runtime_allowed_mount_prefixes() == [
        '/opt/openhands-runtime'
    ]


def test_remote_runtime_allowed_mount_prefixes_accept_json_list():
    config = Settings(
        REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES='["/opt/runtime-a", "/opt/runtime-b"]'
    )

    assert config.get_remote_runtime_allowed_mount_prefixes() == [
        '/opt/runtime-a',
        '/opt/runtime-b',
    ]


def test_remote_runtime_public_host_defaults_localhost_for_wildcard(monkeypatch):
    monkeypatch.delenv('PUBLIC_HOST', raising=False)

    assert resolve_public_host('0.0.0.0') == 'localhost'


def test_remote_runtime_public_host_prefers_environment(monkeypatch):
    monkeypatch.setenv('PUBLIC_HOST', 'ec2.example.test')

    assert resolve_public_host('0.0.0.0') == 'ec2.example.test'


def test_codeact_runtime_plugins_follow_jupyter_config():
    enabled_plugins = CodeActAgent.get_sandbox_plugins(
        AgentConfig(enable_jupyter=True)
    )
    disabled_plugins = CodeActAgent.get_sandbox_plugins(
        AgentConfig(enable_jupyter=False)
    )

    assert [plugin.name for plugin in enabled_plugins] == ['agent_skills', 'jupyter']
    assert disabled_plugins == []


def test_action_server_import_does_not_require_client_heavy_dependencies():
    code = """
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    blocked = {
        'aiodocker',
        'anthropic',
        'boto3',
        'botocore',
        'datasets',
        'daytona_api_client',
        'daytona_sdk',
        'docker',
        'e2b',
        'google',
        'httpx',
        'jinja2',
        'litellm',
        'memory_profiler',
        'minio',
        'modal',
        'numpy',
        'pandas',
        'pathspec',
        'pexpect',
        'redis',
        'runloop_api_client',
        'stripe',
        'swebench',
        'swegym',
        'toml',
    }

    def find_spec(self, fullname, path, target=None):
        if fullname.split('.', 1)[0] in self.blocked:
            raise ImportError(f'blocked import: {fullname}')
        return None

sys.meta_path.insert(0, Blocker())
from openhands.runtime.action_execution_server import _get_plugin_class
from openhands.runtime.utils import find_available_tcp_port
assert _get_plugin_class('jupyter').__name__ == 'JupyterPlugin'
assert callable(find_available_tcp_port)
"""
    subprocess.run(
        [sys.executable, '-c', code],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def test_mounted_runtime_bundle_installs_tmux():
    script = Path('containers/runtime/build_mounted_runtime_bundle.sh').read_text()

    assert (
        'micromamba install -y -p "${CONTAINER_PATH}/env" -c conda-forge poetry tmux setuptools wheel'
        in script
    )
    assert (
        'find "${CONTAINER_PATH}/env/lib" -path "*/site-packages/distutils-precedence.pth" -delete'
        in script
    )


def test_bash_session_prepends_mounted_runtime_path_before_tmux(monkeypatch):
    from openhands.runtime.utils import bash as bash_mod

    class StopInitialize(Exception):
        pass

    captured_env = {}

    def fake_server():
        captured_env['PATH'] = os.environ['PATH']
        captured_env['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
        raise StopInitialize

    monkeypatch.setenv(
        'OPENHANDS_RUNTIME_PYTHON', '/opt/openhands-runtime/bin/python'
    )
    monkeypatch.setenv('OPENHANDS_RUNTIME_WORKING_DIR', '/opt/openhands-runtime')
    monkeypatch.delenv('LD_LIBRARY_PATH', raising=False)
    monkeypatch.setattr(bash_mod.libtmux, 'Server', fake_server)

    with pytest.raises(StopInitialize):
        bash_mod.BashSession('/workspace').initialize()

    assert captured_env['PATH'].startswith(
        '/opt/openhands-runtime/bin:/opt/openhands-runtime/env/bin:'
    )
    assert captured_env['LD_LIBRARY_PATH'].startswith('/opt/openhands-runtime/env/lib:')
