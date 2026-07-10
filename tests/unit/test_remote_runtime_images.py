import asyncio
from unittest.mock import MagicMock

from openhands.runtime.builder.remote import RemoteRuntimeBuilder
from openhands.runtime.remote_runtime_server.managers.runtime import RuntimeManager


class Response:
    status_code = 200
    text = ''

    def __init__(self, exists=True):
        self._exists = exists

    def json(self):
        result = {
            'exists': self._exists,
            'url': 'http://runtime-host:3000',
        }
        if self._exists:
            result['image'] = {
                'upload_time': '2026-01-01T00:00:00Z',
                'image_size_bytes': 123,
            }
        return result


def test_remote_image_check_forwards_local_only_flag(monkeypatch):
    request = MagicMock(return_value=Response(exists=False))
    monkeypatch.setattr('openhands.runtime.builder.remote.send_request', request)
    builder = RemoteRuntimeBuilder(
        'http://gateway:3000', 'test-key', session=MagicMock(), timeout=120
    )

    assert builder.image_exists('registry.example/task:1', False) is False
    assert request.call_args.kwargs['params'] == {
        'image': 'registry.example/task:1',
        'pull_from_repo': False,
    }
    assert request.call_args.kwargs['timeout'] == 120


def test_remote_image_check_allows_long_running_pull(monkeypatch):
    request = MagicMock(return_value=Response())
    monkeypatch.setattr('openhands.runtime.builder.remote.send_request', request)
    builder = RemoteRuntimeBuilder(
        'http://gateway:3000', 'test-key', session=MagicMock(), timeout=120
    )

    assert builder.image_exists('registry.example/task:1') is True
    assert request.call_args.kwargs['params']['pull_from_repo'] is True
    assert request.call_args.kwargs['timeout'] >= 30 * 60


def make_runtime_manager(image_exists):
    manager = RuntimeManager.__new__(RuntimeManager)
    manager._image_pull_locks = {}
    manager._image_builder = MagicMock()
    manager._image_builder.image_exists.side_effect = image_exists
    manager.sync_docker_client = MagicMock()
    image = MagicMock()
    image.attrs = {'Created': '2026-01-01T00:00:00Z', 'Size': 123}
    manager.sync_docker_client.images.get.return_value = image
    return manager


def use_direct_sync_bridge(monkeypatch):
    async def call_direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(
        'openhands.runtime.remote_runtime_server.managers.runtime.call_sync_from_async',
        call_direct,
    )


def test_remote_image_manager_preserves_local_only_miss(monkeypatch):
    use_direct_sync_bridge(monkeypatch)
    manager = make_runtime_manager(lambda image, pull: False)

    result = asyncio.run(
        manager.ensure_image('registry.example/task:1', pull_from_repo=False)
    )

    assert result is None
    manager._image_builder.image_exists.assert_called_once_with(
        'registry.example/task:1', False
    )
    manager.sync_docker_client.images.get.assert_not_called()


def test_remote_image_manager_returns_pulled_image_metadata(monkeypatch):
    use_direct_sync_bridge(monkeypatch)
    manager = make_runtime_manager(lambda image, pull: True)

    result = asyncio.run(manager.ensure_image('registry.example/task:1'))

    assert result == {
        'upload_time': '2026-01-01T00:00:00Z',
        'image_size_bytes': 123,
    }
    manager._image_builder.image_exists.assert_called_once_with(
        'registry.example/task:1', True
    )


def test_remote_image_manager_serializes_same_image_pull(monkeypatch):
    active_calls = 0
    max_active_calls = 0

    async def tracked_sync_bridge(function, *args, **kwargs):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        result = function(*args, **kwargs)
        active_calls -= 1
        return result

    monkeypatch.setattr(
        'openhands.runtime.remote_runtime_server.managers.runtime.call_sync_from_async',
        tracked_sync_bridge,
    )

    manager = make_runtime_manager(lambda image, pull: True)

    async def ensure_twice():
        await asyncio.gather(
            manager.ensure_image('registry.example/task:1'),
            manager.ensure_image('registry.example/task:1'),
        )

    asyncio.run(ensure_twice())

    assert max_active_calls == 1
