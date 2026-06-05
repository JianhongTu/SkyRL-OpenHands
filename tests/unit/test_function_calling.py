"""Test function calling module."""

import json

import pytest
from litellm import ModelResponse

from openhands.agenthub.codeact_agent.function_calling import response_to_actions
from openhands.core.exceptions import FunctionCallValidationError
from openhands.events.action import (
    AgentFinishAction,
    BrowseInteractiveAction,
    BrowseURLAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    IPythonRunCellAction,
    SearchAction,
)
from openhands.events.event import FileEditSource, FileReadSource
from openhands.events.serialization.event import event_to_dict


def create_mock_response(function_name: str, arguments: dict) -> ModelResponse:
    """Helper function to create a mock response with a tool call."""
    return ModelResponse(
        id='mock-id',
        choices=[
            {
                'message': {
                    'tool_calls': [
                        {
                            'function': {
                                'name': function_name,
                                'arguments': json.dumps(arguments),
                            },
                            'id': 'mock-tool-call-id',
                            'type': 'function',
                        }
                    ],
                    'content': None,
                    'role': 'assistant',
                },
                'index': 0,
                'finish_reason': 'tool_calls',
            }
        ],
    )


def create_text_response(content: str) -> ModelResponse:
    return ModelResponse(
        id='mock-id',
        choices=[
            {
                'message': {
                    'content': content,
                    'role': 'assistant',
                },
                'index': 0,
                'finish_reason': 'stop',
            }
        ],
    )


def test_qwen_tool_call_content_is_converted_to_action():
    response = create_text_response(
        'I will inspect the repo.\n<tool_call>\n'
        '{"name": "execute_bash", "arguments": {"command": "ls"}}\n'
        '</tool_call>'
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], CmdRunAction)
    assert actions[0].command == 'ls'
    assert actions[0].thought == 'I will inspect the repo.'
    assert actions[0].tool_call_metadata.tool_call_id == 'toolu_01'


def test_qwen_tool_call_action_metadata_is_serializable():
    response = create_text_response(
        '<tool_call>\n'
        '{"name": "execute_bash", "arguments": {"command": "ls"}}\n'
        '</tool_call>'
    )
    action = response_to_actions(response)[0]
    serialized = event_to_dict(action)
    tool_calls = serialized['tool_call_metadata']['model_response']['choices'][0][
        'message'
    ]['tool_calls']
    assert tool_calls == [
        {
            'function': {'arguments': '{"command": "ls"}', 'name': 'execute_bash'},
            'id': 'toolu_01',
            'type': 'function',
        }
    ]


def test_qwen_finish_requires_message_and_normalizes_bool():
    response = create_text_response(
        '<tool_call>\n'
        '{"name": "finish", "arguments": {"message": "done", "task_completed": true}}\n'
        '</tool_call>'
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], AgentFinishAction)
    assert actions[0].final_thought == 'done'
    assert actions[0].task_completed == 'true'


def test_qwen_tool_call_rejects_trailing_text():
    response = create_text_response(
        '<tool_call>\n'
        '{"name": "execute_bash", "arguments": {"command": "ls"}}\n'
        '</tool_call>\nextra'
    )
    with pytest.raises(FunctionCallValidationError):
        response_to_actions(response)


def test_qwen_finish_rejects_missing_message():
    response = create_text_response(
        '<tool_call>\n'
        '{"name": "finish", "arguments": {"task_completed": "true"}}\n'
        '</tool_call>'
    )
    with pytest.raises(FunctionCallValidationError):
        response_to_actions(response)


def test_execute_bash_valid():
    """Test execute_bash with valid arguments."""
    response = create_mock_response(
        'execute_bash', {'command': 'ls', 'is_input': 'false'}
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], CmdRunAction)
    assert actions[0].command == 'ls'
    assert actions[0].is_input is False


def test_execute_bash_missing_command():
    """Test execute_bash with missing command argument."""
    response = create_mock_response('execute_bash', {'is_input': 'false'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "command"' in str(exc_info.value)


def test_execute_ipython_cell_valid():
    """Test execute_ipython_cell with valid arguments."""
    response = create_mock_response('execute_ipython_cell', {'code': "print('hello')"})
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], IPythonRunCellAction)
    assert actions[0].code == "print('hello')"


def test_execute_ipython_cell_missing_code():
    """Test execute_ipython_cell with missing code argument."""
    response = create_mock_response('execute_ipython_cell', {})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "code"' in str(exc_info.value)


def test_edit_file_valid():
    """Test edit_file with valid arguments."""
    response = create_mock_response(
        'edit_file',
        {'path': '/path/to/file', 'content': 'file content', 'start': 1, 'end': 10},
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], FileEditAction)
    assert actions[0].path == '/path/to/file'
    assert actions[0].content == 'file content'
    assert actions[0].start == 1
    assert actions[0].end == 10


def test_edit_file_missing_required():
    """Test edit_file with missing required arguments."""
    # Missing path
    response = create_mock_response('edit_file', {'content': 'content'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "path"' in str(exc_info.value)

    # Missing content
    response = create_mock_response('edit_file', {'path': '/path/to/file'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "content"' in str(exc_info.value)


def test_str_replace_editor_valid():
    """Test str_replace_editor with valid arguments."""
    # Test view command
    response = create_mock_response(
        'str_replace_editor', {'command': 'view', 'path': '/path/to/file'}
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], FileReadAction)
    assert actions[0].path == '/path/to/file'
    assert actions[0].impl_source == FileReadSource.OH_ACI

    # Test other commands
    response = create_mock_response(
        'str_replace_editor',
        {
            'command': 'str_replace',
            'path': '/path/to/file',
            'old_str': 'old',
            'new_str': 'new',
        },
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], FileEditAction)
    assert actions[0].path == '/path/to/file'
    assert actions[0].impl_source == FileEditSource.OH_ACI


def test_str_replace_editor_missing_required():
    """Test str_replace_editor with missing required arguments."""
    # Missing command
    response = create_mock_response('str_replace_editor', {'path': '/path/to/file'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "command"' in str(exc_info.value)

    # Missing path
    response = create_mock_response('str_replace_editor', {'command': 'view'})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "path"' in str(exc_info.value)


def test_browser_valid():
    """Test browser with valid arguments."""
    response = create_mock_response('browser', {'code': "click('button-1')"})
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], BrowseInteractiveAction)
    assert actions[0].browser_actions == "click('button-1')"


def test_browser_missing_code():
    """Test browser with missing code argument."""
    response = create_mock_response('browser', {})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "code"' in str(exc_info.value)


def test_web_read_valid():
    """Test web_read with valid arguments."""
    response = create_mock_response('web_read', {'url': 'https://example.com'})
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], BrowseURLAction)
    assert actions[0].url == 'https://example.com'


def test_web_read_missing_url():
    """Test web_read with missing url argument."""
    response = create_mock_response('web_read', {})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "url"' in str(exc_info.value)


def test_search_with_line_nums_valid():
    """Test search with line number context arguments."""
    response = create_mock_response(
        'search',
        {'line_nums': [42, 43], 'file_path_or_pattern': '/path/to/file.py'},
    )
    actions = response_to_actions(response)
    assert len(actions) == 1
    assert isinstance(actions[0], SearchAction)
    assert actions[0].line_nums == [42, 43]
    assert actions[0].search_terms is None
    assert actions[0].file_path_or_pattern == '/path/to/file.py'


def test_search_missing_terms_and_lines():
    """Test search requires either search terms or line numbers."""
    response = create_mock_response('search', {})
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Missing required argument "search_terms" or "line_nums"' in str(
        exc_info.value
    )


def test_invalid_json_arguments():
    """Test handling of invalid JSON in arguments."""
    response = ModelResponse(
        id='mock-id',
        choices=[
            {
                'message': {
                    'tool_calls': [
                        {
                            'function': {
                                'name': 'execute_bash',
                                'arguments': 'invalid json',
                            },
                            'id': 'mock-tool-call-id',
                            'type': 'function',
                        }
                    ],
                    'content': None,
                    'role': 'assistant',
                },
                'index': 0,
                'finish_reason': 'tool_calls',
            }
        ],
    )
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)
    assert 'Failed to parse tool call arguments' in str(exc_info.value)


def test_unexpected_argument_handling():
    """Test that unexpected arguments in function calls are properly handled.

    This test reproduces issue #8369 Example 4 where an unexpected argument
    (old_str_prefix) causes a TypeError.
    """
    response = create_mock_response(
        'str_replace_editor',
        {
            'command': 'str_replace',
            'path': '/test/file.py',
            'old_str': 'def test():\n    pass',
            'new_str': 'def test():\n    return True',
            'old_str_prefix': 'some prefix',  # Unexpected argument
        },
    )

    # Test that the function raises a FunctionCallValidationError
    with pytest.raises(FunctionCallValidationError) as exc_info:
        response_to_actions(response)

    # Verify the error message mentions the unexpected argument
    assert 'old_str_prefix' in str(exc_info.value)
    assert 'Unexpected argument' in str(exc_info.value)
