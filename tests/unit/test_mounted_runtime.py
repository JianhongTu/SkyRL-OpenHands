import asyncio
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from evaluation.benchmarks.swe_bench.eval_infer import get_config as get_eval_config
from evaluation.benchmarks.swe_bench.run_infer import (
    complete_runtime,
    instance_to_json_safe_dict,
)
from openhands.agenthub.codeact_agent.codeact_agent import CodeActAgent
from openhands.core.config.agent_config import AgentConfig
from openhands.core.config.app_config import AppConfig
from openhands.core.config.sandbox_config import SandboxConfig
from openhands.events.observation import CmdOutputObservation
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


def test_swe_completion_downloads_patch_without_shell_output(tmp_path):
    patch = 'diff --git a/example.py b/example.py\n'
    patch_archive = tmp_path / 'patch.zip'
    with ZipFile(patch_archive, 'w') as archive:
        archive.writestr('patch.diff', patch)
    observations = [
        CmdOutputObservation(content='', command='', exit_code=0)
        for _ in range(7)
    ]
    actions = []

    class Runtime:
        copied_path = None

        def run_action(self, action):
            actions.append(action)
            return observations.pop(0)

        def copy_from(self, path):
            self.copied_path = path
            return patch_archive

    runtime = Runtime()
    result = complete_runtime(
        runtime,
        pd.Series(
            {
                'repo': 'scikit-learn/scikit-learn',
                'version': '0.24',
                'base_commit': 'abc123',
            }
        ),
        SimpleNamespace(dataset='princeton-nlp/SWE-bench_Lite', details={'mode': 'swe'}),
    )

    assert runtime.copied_path == '/tmp/openhands-eval-patch'
    assert 'cp patch.diff /tmp/openhands-eval-patch/patch.diff' in actions[-1].command
    assert result == {'git_patch': patch.rstrip()}


def test_swe_eval_config_passes_instance_and_dataset_to_image_resolver(monkeypatch):
    calls = []

    def resolve_image(instance, data_source):
        calls.append((instance, data_source))
        return 'example/image:latest'

    monkeypatch.setattr(
        'evaluation.benchmarks.swe_bench.eval_infer.get_instance_docker_image',
        resolve_image,
    )
    instance = pd.Series({'instance_id': 'owner__repo-1'})
    metadata = SimpleNamespace(dataset='Princeton-NLP/SWE-bench_Lite')

    config = get_eval_config(metadata, instance)

    assert config.sandbox.base_container_image == 'example/image:latest'
    assert calls == [(instance, 'princeton-nlp/swe-bench_lite')]


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


def _create_runtime_bundle(path, container_path='/opt/openhands-runtime'):
    executable_paths = (
        'bin/python',
        'env/bin/python',
        'tools/bin/search',
        'tools/bin/str_replace_editor',
    )
    for relative_path in executable_paths:
        executable = path / relative_path
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text('#!/bin/sh\n')
        executable.chmod(0o755)
    metadata = path / 'meta/container_path'
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(container_path + '\n')


def test_remote_runtime_bundle_preflight_accepts_valid_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    _create_runtime_bundle(bundle)
    manager = RuntimeManager.__new__(RuntimeManager)
    monkeypatch.setattr(
        settings, 'REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES', [str(tmp_path)]
    )

    binds = manager._get_runtime_mount_binds(
        [
            RuntimeMount(
                host_path=str(bundle),
                container_path='/opt/openhands-runtime',
            )
        ]
    )

    assert binds == [f'{bundle}:/opt/openhands-runtime:ro']


def test_remote_runtime_bundle_preflight_rejects_incomplete_bundle(
    tmp_path, monkeypatch
):
    bundle = tmp_path / 'bundle'
    _create_runtime_bundle(bundle)
    (bundle / 'tools/bin/search').unlink()
    manager = RuntimeManager.__new__(RuntimeManager)
    monkeypatch.setattr(
        settings, 'REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES', [str(tmp_path)]
    )

    with pytest.raises(HTTPException) as exc_info:
        manager._get_runtime_mount_binds(
            [
                RuntimeMount(
                    host_path=str(bundle),
                    container_path='/opt/openhands-runtime',
                )
            ]
        )

    assert getattr(exc_info.value, 'status_code', None) == 400
    assert 'tools/bin/search' in str(exc_info.value.detail)


def test_remote_runtime_bundle_preflight_rejects_container_path_mismatch(
    tmp_path, monkeypatch
):
    bundle = tmp_path / 'bundle'
    _create_runtime_bundle(bundle, container_path='/different/path')
    manager = RuntimeManager.__new__(RuntimeManager)
    monkeypatch.setattr(
        settings, 'REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES', [str(tmp_path)]
    )

    with pytest.raises(HTTPException) as exc_info:
        manager._get_runtime_mount_binds(
            [
                RuntimeMount(
                    host_path=str(bundle),
                    container_path='/opt/openhands-runtime',
                )
            ]
        )

    assert getattr(exc_info.value, 'status_code', None) == 400
    assert 'built for container path /different/path' in str(exc_info.value.detail)


def test_remote_runtime_bundle_preflight_runs_before_port_allocation(
    tmp_path, monkeypatch
):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    manager = RuntimeManager.__new__(RuntimeManager)
    monkeypatch.setattr(
        settings, 'REMOTE_RUNTIME_ALLOWED_MOUNT_PREFIXES', [str(tmp_path)]
    )
    port_allocation_called = False

    async def get_ports(*args, **kwargs):
        nonlocal port_allocation_called
        port_allocation_called = True
        return 30000, 40000, [50000, 55000]

    monkeypatch.setattr(manager, '_get_ports', get_ports)
    request = StartRequest(
        image='ubuntu:22.04',
        command=['/opt/openhands-runtime/bin/python'],
        working_dir='/opt/openhands-runtime',
        environment={},
        session_id='test-session',
        resource_factor=1,
        runtime_mounts=[
            RuntimeMount(
                host_path=str(bundle),
                container_path='/opt/openhands-runtime',
            )
        ],
    )

    with pytest.raises(HTTPException):
        asyncio.run(manager.start_runtime(None, request))

    assert port_allocation_called is False


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
    assert [plugin.name for plugin in disabled_plugins] == ['agent_skills']


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


def test_runtime_core_includes_agent_skills_dependencies():
    pyproject = tomllib.loads(Path('pyproject.toml').read_text())

    assert 'openai' in pyproject['dependency-groups']['runtime-core']


def test_runtime_groups_include_skyrl_tool_dependencies():
    pyproject = tomllib.loads(Path('pyproject.toml').read_text())

    for group in ('runtime-core', 'runtime'):
        dependencies = pyproject['dependency-groups'][group]
        assert 'chardet' in dependencies
        assert 'networkx' in dependencies
        assert 'rank-bm25>=0.2.0,<1.0.0' in dependencies


def test_runtime_tool_sources_and_delivery_are_wired():
    for tool_name in ('search', 'str_replace_editor'):
        tool_path = Path(f'openhands/runtime/tools/{tool_name}.py')
        assert tool_path.is_file()
        assert 'def main()' in tool_path.read_text()

    bundle_script = Path(
        'containers/runtime/build_mounted_runtime_bundle.sh'
    ).read_text()
    assert '"${CONTAINER_PATH}/tools/bin/search"' in bundle_script
    assert '"${CONTAINER_PATH}/tools/bin/str_replace_editor"' in bundle_script

    dockerfile = Path(
        'openhands/runtime/utils/runtime_templates/Dockerfile.j2'
    ).read_text()
    assert '/openhands/bin/search' in dockerfile
    assert '/openhands/bin/str_replace_editor' in dockerfile


def test_bash_session_keeps_mounted_runtime_path_out_of_shell(monkeypatch):
    from openhands.runtime.utils import bash as bash_mod

    class FakePane:
        def __init__(self):
            self.commands = []

        def send_keys(self, command, **kwargs):
            self.commands.append(command)

        def cmd(self, *args):
            return SimpleNamespace(stdout=[])

    class FakeWindow:
        def __init__(self, pane=None):
            self.active_pane = pane

        def kill_window(self):
            pass

    class FakeSession:
        def __init__(self):
            self.pane = FakePane()
            self.active_window = FakeWindow()
            self.history_limit = 0

        def set_option(self, *args, **kwargs):
            pass

        def new_window(self, **kwargs):
            return FakeWindow(self.pane)

        def kill_session(self):
            pass

    class FakeServer:
        def new_session(self, **kwargs):
            return FakeSession()

    captured_env = {}

    def fake_server():
        captured_env['PATH'] = os.environ['PATH']
        captured_env['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
        return FakeServer()

    monkeypatch.setenv(
        'OPENHANDS_RUNTIME_PYTHON', '/opt/openhands-runtime/bin/python'
    )
    monkeypatch.setenv('OPENHANDS_RUNTIME_WORKING_DIR', '/opt/openhands-runtime')
    monkeypatch.setenv('PATH', '/testbed/bin:/usr/bin')
    monkeypatch.setenv('LD_LIBRARY_PATH', '/testbed/lib')
    monkeypatch.setattr(bash_mod.libtmux, 'Server', fake_server)

    session = bash_mod.BashSession('/workspace')
    session.initialize()

    assert captured_env['PATH'].startswith(
        '/opt/openhands-runtime/bin:/opt/openhands-runtime/env/bin:'
    )
    assert captured_env['LD_LIBRARY_PATH'].startswith('/opt/openhands-runtime/env/lib:')
    assert (
        'export PATH=/opt/openhands-runtime/tools/bin:/testbed/bin:/usr/bin'
        in session.pane.commands
    )
    assert 'export LD_LIBRARY_PATH=/testbed/lib' in session.pane.commands
    session.close()
