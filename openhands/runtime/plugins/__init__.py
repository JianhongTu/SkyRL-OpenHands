from importlib import import_module
from typing import TYPE_CHECKING, Any

from openhands.runtime.plugins.requirement import Plugin, PluginRequirement

if TYPE_CHECKING:
    from openhands.runtime.plugins.agent_skills import (
        AgentSkillsPlugin,
        AgentSkillsRequirement,
    )
    from openhands.runtime.plugins.jupyter import JupyterPlugin, JupyterRequirement
    from openhands.runtime.plugins.vscode import VSCodePlugin, VSCodeRequirement

_PLUGIN_EXPORTS = {
    'AgentSkillsRequirement': 'openhands.runtime.plugins.agent_skills:AgentSkillsRequirement',
    'AgentSkillsPlugin': 'openhands.runtime.plugins.agent_skills:AgentSkillsPlugin',
    'JupyterRequirement': 'openhands.runtime.plugins.jupyter:JupyterRequirement',
    'JupyterPlugin': 'openhands.runtime.plugins.jupyter:JupyterPlugin',
    'VSCodeRequirement': 'openhands.runtime.plugins.vscode:VSCodeRequirement',
    'VSCodePlugin': 'openhands.runtime.plugins.vscode:VSCodePlugin',
}


def _load_export(name: str) -> Any:
    module_name, attribute_name = _PLUGIN_EXPORTS[name].split(':', 1)
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __getattr__(name: str) -> Any:
    if name == 'ALL_PLUGINS':
        value = {
            'jupyter': _load_export('JupyterPlugin'),
            'agent_skills': _load_export('AgentSkillsPlugin'),
            'vscode': _load_export('VSCodePlugin'),
        }
        globals()[name] = value
        return value
    if name in _PLUGIN_EXPORTS:
        return _load_export(name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

__all__ = [
    'Plugin',
    'PluginRequirement',
    'AgentSkillsRequirement',
    'AgentSkillsPlugin',
    'JupyterRequirement',
    'JupyterPlugin',
    'VSCodeRequirement',
    'VSCodePlugin',
    'ALL_PLUGINS',
]
