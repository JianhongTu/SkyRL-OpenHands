from importlib import import_module
from typing import TYPE_CHECKING, Any

from openhands.utils.import_utils import get_impl

if TYPE_CHECKING:
    from openhands.runtime.base import Runtime
    from openhands.runtime.impl.daytona.daytona_runtime import DaytonaRuntime
    from openhands.runtime.impl.docker.docker_runtime import DockerRuntime
    from openhands.runtime.impl.e2b.e2b_runtime import E2BRuntime
    from openhands.runtime.impl.local.local_runtime import LocalRuntime
    from openhands.runtime.impl.modal.modal_runtime import ModalRuntime
    from openhands.runtime.impl.remote.remote_runtime import RemoteRuntime
    from openhands.runtime.impl.runloop.runloop_runtime import RunloopRuntime

# mypy: disable-error-code="type-abstract"
_RUNTIME_CLASS_PATHS = {
    'eventstream': 'openhands.runtime.impl.docker.docker_runtime:DockerRuntime',
    'docker': 'openhands.runtime.impl.docker.docker_runtime:DockerRuntime',
    'e2b': 'openhands.runtime.impl.e2b.e2b_runtime:E2BRuntime',
    'remote': 'openhands.runtime.impl.remote.remote_runtime:RemoteRuntime',
    'modal': 'openhands.runtime.impl.modal.modal_runtime:ModalRuntime',
    'runloop': 'openhands.runtime.impl.runloop.runloop_runtime:RunloopRuntime',
    'local': 'openhands.runtime.impl.local.local_runtime:LocalRuntime',
    'daytona': 'openhands.runtime.impl.daytona.daytona_runtime:DaytonaRuntime',
}

_RUNTIME_EXPORTS = {
    'Runtime': 'openhands.runtime.base:Runtime',
    'DockerRuntime': 'openhands.runtime.impl.docker.docker_runtime:DockerRuntime',
    'E2BRuntime': 'openhands.runtime.impl.e2b.e2b_runtime:E2BRuntime',
    'RemoteRuntime': 'openhands.runtime.impl.remote.remote_runtime:RemoteRuntime',
    'ModalRuntime': 'openhands.runtime.impl.modal.modal_runtime:ModalRuntime',
    'RunloopRuntime': 'openhands.runtime.impl.runloop.runloop_runtime:RunloopRuntime',
    'LocalRuntime': 'openhands.runtime.impl.local.local_runtime:LocalRuntime',
    'DaytonaRuntime': 'openhands.runtime.impl.daytona.daytona_runtime:DaytonaRuntime',
}


def _load_runtime_class(path: str) -> Any:
    module_name, class_name = path.split(':', 1)
    module = import_module(module_name)
    return getattr(module, class_name)


def get_runtime_cls(name: str) -> type['Runtime']:
    """
    If name is one of the predefined runtime names (e.g. 'docker'), return its class.
    Otherwise attempt to resolve name as subclass of Runtime and return it.
    Raise on invalid selections.
    """
    if name in _RUNTIME_CLASS_PATHS:
        return _load_runtime_class(_RUNTIME_CLASS_PATHS[name])
    try:
        return get_impl(_load_runtime_class(_RUNTIME_EXPORTS['Runtime']), name)
    except Exception as e:
        known_keys = _RUNTIME_CLASS_PATHS.keys()
        raise ValueError(
            f'Runtime {name} not supported, known are: {known_keys}'
        ) from e


def __getattr__(name: str):
    if name in _RUNTIME_EXPORTS:
        return _load_runtime_class(_RUNTIME_EXPORTS[name])
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


__all__ = [
    'Runtime',
    'E2BRuntime',
    'RemoteRuntime',
    'ModalRuntime',
    'RunloopRuntime',
    'DockerRuntime',
    'DaytonaRuntime',
    'get_runtime_cls',
]
