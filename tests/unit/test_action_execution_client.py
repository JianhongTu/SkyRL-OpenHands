import shlex

from openhands.events.action import CmdRunAction, FileReadAction
from openhands.events.action.search import SearchAction
from openhands.runtime.impl.action_execution.action_execution_client import (
    ActionExecutionClient,
)


class CapturingActionExecutionClient(ActionExecutionClient):
    async def connect(self):
        pass


def _capturing_client() -> tuple[ActionExecutionClient, list[object]]:
    client = object.__new__(CapturingActionExecutionClient)
    actions: list[object] = []
    client.send_action_for_execution = lambda action: actions.append(action) or action
    return client, actions


def test_read_quotes_path_for_str_replace_editor_command():
    client, actions = _capturing_client()

    client.read(FileReadAction(path="/tmp/a b/quote'"))

    assert isinstance(actions[0], CmdRunAction)
    assert shlex.split(actions[0].command) == [
        'str_replace_editor',
        'view',
        '--path',
        "/tmp/a b/quote'",
    ]


def test_search_quotes_arguments_and_forwards_line_nums():
    client, actions = _capturing_client()

    client.search(
        SearchAction(
            search_terms=["quote' term"],
            line_nums=[7],
            file_path_or_pattern='/tmp/a b.py',
        )
    )

    assert shlex.split(actions[0].command) == [
        'search',
        '--file_path_or_pattern',
        '/tmp/a b.py',
        '--search_terms',
        '["quote\' term"]',
        '--line_nums',
        '[7]',
    ]
