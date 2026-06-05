import shlex

from openhands.events.action import FileReadAction
from openhands.events.action.search import SearchAction
from openhands.runtime.impl.action_execution.action_execution_client import (
    ActionExecutionClient,
)


class CapturingActionExecutionClient(ActionExecutionClient):
    async def connect(self):
        pass


def _capturing_client() -> tuple[ActionExecutionClient, list[str]]:
    client = object.__new__(CapturingActionExecutionClient)
    commands: list[str] = []
    client.send_action_for_execution = lambda action: commands.append(
        action.command
    ) or action
    return client, commands


def test_read_quotes_path_for_str_replace_editor_command():
    client, commands = _capturing_client()

    client.read(FileReadAction(path="/tmp/a b/quote'"))

    assert shlex.split(commands[0]) == [
        'str_replace_editor',
        'view',
        '--path',
        "/tmp/a b/quote'",
    ]


def test_search_quotes_arguments_and_forwards_line_nums():
    client, commands = _capturing_client()

    client.search(
        SearchAction(
            search_terms=["quote' term"],
            line_nums=[7],
            file_path_or_pattern='/tmp/a b.py',
        )
    )

    assert shlex.split(commands[0]) == [
        'search',
        '--file_path_or_pattern',
        '/tmp/a b.py',
        '--search_terms',
        '["quote\' term"]',
        '--line_nums',
        '[7]',
    ]
