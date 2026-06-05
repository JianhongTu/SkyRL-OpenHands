"""This file contains the function calling implementation for different actions.

This is similar to the functionality of `CodeActResponseParser`.
"""

import json
import re
from types import SimpleNamespace

from litellm import (
    ChatCompletionMessageToolCall,
    ModelResponse,
)

from openhands.agenthub.codeact_agent.tools import (
    BrowserTool,
    FinishTool,
    IPythonTool,
    LLMBasedFileEditTool,
    ThinkTool,
    WebReadTool,
    create_cmd_run_tool,
    create_str_replace_editor_tool,
    create_search_files_tool,
)
from openhands.core.exceptions import (
    FunctionCallNotExistsError,
    FunctionCallValidationError,
)
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    Action,
    AgentDelegateAction,
    AgentFinishAction,
    AgentThinkAction,
    BrowseInteractiveAction,
    BrowseURLAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    IPythonRunCellAction,
    MessageAction,
    SearchAction,
)
from openhands.events.action.mcp import MCPAction
from openhands.events.event import FileEditSource, FileReadSource
from openhands.events.tool import ToolCallMetadata


QWEN_TOOL_CALL_REGEX = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL)

QWEN_TOOL_CALL_FORMAT_ERROR = """Your previous response could not be parsed as a tool call.

Use exactly one tool call in this format:
<tool_call>
{"name": "execute_bash", "arguments": {"command": "ls"}}
</tool_call>

For finishing:
<tool_call>
{"name": "finish", "arguments": {"message": "Done", "task_completed": "true"}}
</tool_call>"""


def _normalize_finish_arguments(arguments: dict) -> dict:
    normalized = dict(arguments)
    if not set(normalized).issubset({'message', 'task_completed'}):
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)
    if 'message' not in normalized:
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)
    if not isinstance(normalized['message'], str):
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)
    if 'task_completed' in normalized:
        task_completed = normalized['task_completed']
        if isinstance(task_completed, bool):
            normalized['task_completed'] = str(task_completed).lower()
        elif task_completed not in {'true', 'false', 'partial'}:
            raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)
    return normalized


def _convert_qwen_tool_call_content(content: str):
    stripped_content = content.strip()
    matches = list(QWEN_TOOL_CALL_REGEX.finditer(stripped_content))
    if not matches:
        return None
    if len(matches) != 1:
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)

    match = matches[0]
    if match.end() != len(stripped_content):
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)

    try:
        payload = json.loads(match.group(1).strip())
    except json.JSONDecodeError as exc:
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR) from exc
    if not isinstance(payload, dict) or set(payload) != {'name', 'arguments'}:
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)

    fn_name = payload['name']
    arguments = payload['arguments']
    if not isinstance(fn_name, str) or not fn_name:
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)
    if not isinstance(arguments, dict):
        raise FunctionCallValidationError(QWEN_TOOL_CALL_FORMAT_ERROR)
    if fn_name == FinishTool['function']['name']:
        arguments = _normalize_finish_arguments(arguments)

    return SimpleNamespace(
        content=stripped_content[: match.start()].strip(),
        tool_calls=[
            ChatCompletionMessageToolCall(
                id='toolu_01',
                type='function',
                function={
                    'name': fn_name,
                    'arguments': json.dumps(arguments, ensure_ascii=False),
                },
            )
        ],
    )


def _maybe_convert_qwen_tool_call_response(response: ModelResponse) -> None:
    assistant_msg = response.choices[0].message
    if getattr(assistant_msg, 'tool_calls', None):
        return
    content = getattr(assistant_msg, 'content', None)
    if not isinstance(content, str) or '<tool_call>' not in content:
        return

    converted = _convert_qwen_tool_call_content(content)
    if converted is None:
        return
    assistant_msg.content = converted.content
    assistant_msg.tool_calls = converted.tool_calls


def combine_thought(action: Action, thought: str) -> Action:
    if not hasattr(action, 'thought'):
        return action
    if thought and action.thought:
        action.thought = f'{thought}\n{action.thought}'
    elif thought:
        action.thought = thought
    return action


def response_to_actions(
    response: ModelResponse, mcp_tool_names: list[str] | None = None
) -> list[Action]:
    actions: list[Action] = []
    assert len(response.choices) == 1, 'Only one choice is supported for now'
    _maybe_convert_qwen_tool_call_response(response)
    choice = response.choices[0]
    assistant_msg = choice.message
    if hasattr(assistant_msg, 'tool_calls') and assistant_msg.tool_calls:
        # Check if there's assistant_msg.content. If so, add it to the thought
        thought = ''
        if isinstance(assistant_msg.content, str):
            thought = assistant_msg.content
        elif isinstance(assistant_msg.content, list):
            for msg in assistant_msg.content:
                if msg['type'] == 'text':
                    thought += msg['text']

        # Process each tool call to OpenHands action
        for i, tool_call in enumerate(assistant_msg.tool_calls):
            action: Action
            logger.debug(f'Tool call in function_calling.py: {tool_call}')
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.decoder.JSONDecodeError as e:
                raise FunctionCallValidationError(
                    f'Failed to parse tool call arguments: {tool_call.function.arguments}'
                ) from e

            # ================================================
            # CmdRunTool (Bash)
            # ================================================

            if tool_call.function.name == create_cmd_run_tool()['function']['name']:
                if 'command' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "command" in tool call {tool_call.function.name}'
                    )
                # convert is_input to boolean
                is_input = arguments.get('is_input', 'false') == 'true'
                action = CmdRunAction(command=arguments['command'], is_input=is_input)

            # ================================================
            # IPythonTool (Jupyter)
            # ================================================
            elif tool_call.function.name == IPythonTool['function']['name']:
                if 'code' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "code" in tool call {tool_call.function.name}'
                    )
                action = IPythonRunCellAction(code=arguments['code'])
            elif tool_call.function.name == 'delegate_to_browsing_agent':
                action = AgentDelegateAction(
                    agent='BrowsingAgent',
                    inputs=arguments,
                )

            # ================================================
            # AgentFinishAction
            # ================================================
            elif tool_call.function.name == FinishTool['function']['name']:
                action = AgentFinishAction(
                    final_thought=arguments.get('message', ''),
                    task_completed=arguments.get('task_completed', None),
                )

            # ================================================
            # SearchFilesTool
            # ================================================
            elif tool_call.function.name == create_search_files_tool()['function']['name']:
                # if 'search_term' not in arguments:
                #     raise FunctionCallValidationError(
                #         f'Missing required argument "search_term" in tool call {tool_call.function.name}'
                #     )

                # action = SearchAction(
                #     search_term=arguments['search_term'],
                #     path=arguments.get('path', '.'),
                #     python_only=arguments.get('python_only', 'false'),
                # )
                # either search_terms or line_nums must be provided
                if 'search_terms' not in arguments and 'line_nums' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "search_terms" or "line_nums" in tool call {tool_call.function.name}'
                    )
                # if not isinstance(arguments['search_terms'], list):
                #     raise FunctionCallValidationError(
                #         f'Invalid format for argument "search_terms" in tool call {tool_call.function.name}. Expected a list of strings.'
                #     )
                search_terms = arguments.get('search_terms', None)
                if isinstance(search_terms, str):
                    search_terms = [search_terms]
                if search_terms is not None and (
                    not isinstance(search_terms, list)
                    or not all(
                        isinstance(search_term, str) for search_term in search_terms
                    )
                ):
                    raise FunctionCallValidationError(
                        f'Invalid format for argument "search_terms" in tool call {tool_call.function.name}. Expected a list of strings.'
                    )
                line_nums = arguments.get('line_nums', None)
                if type(line_nums) is int:
                    line_nums = [line_nums]
                if line_nums is not None and (
                    not isinstance(line_nums, list)
                    or not all(type(line_num) is int for line_num in line_nums)
                ):
                    raise FunctionCallValidationError(
                        f'Invalid format for argument "line_nums" in tool call {tool_call.function.name}. Expected a list of integers.'
                    )
                if search_terms is None and line_nums is None:
                    raise FunctionCallValidationError(
                        f'Missing required argument "search_terms" or "line_nums" in tool call {tool_call.function.name}'
                    )
                action = SearchAction(
                    search_terms=search_terms,
                    line_nums=line_nums,
                    file_path_or_pattern=arguments.get(
                        'file_path_or_pattern', '**/*.py'
                    ),
                )
                
            # ================================================
            # LLMBasedFileEditTool (LLM-based file editor, deprecated)
            # ================================================
            elif tool_call.function.name == LLMBasedFileEditTool['function']['name']:
                if 'path' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "path" in tool call {tool_call.function.name}'
                    )
                if 'content' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "content" in tool call {tool_call.function.name}'
                    )
                action = FileEditAction(
                    path=arguments['path'],
                    content=arguments['content'],
                    start=arguments.get('start', 1),
                    end=arguments.get('end', -1),
                )
            elif (
                tool_call.function.name
                == create_str_replace_editor_tool()['function']['name']
            ):
                if 'command' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "command" in tool call {tool_call.function.name}'
                    )
                if 'path' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "path" in tool call {tool_call.function.name}'
                    )
                path = arguments['path']
                command = arguments['command']
                other_kwargs = {
                    k: v for k, v in arguments.items() if k not in ['command', 'path']
                }

                if command == 'view':
                    # check view_range has valid format ([a,b]) if provided
                    if 'view_range' in other_kwargs:
                        view_range = other_kwargs['view_range']
                        if (
                            not isinstance(view_range, list)
                            or len(view_range) != 2
                            or not all(isinstance(i, int) for i in view_range)
                        ):
                            raise FunctionCallValidationError(
                                f'Invalid format for argument "view_range" in tool call {tool_call.function.name}. Expected format: [start_line, end_line]'
                            )

                    action = FileReadAction(
                        path=path,
                        impl_source=FileReadSource.OH_ACI,
                        view_range=other_kwargs.get('view_range', None),
                        concise=other_kwargs.get('concise', False)
                    )
                else:
                    if 'view_range' in other_kwargs:
                        # Remove view_range from other_kwargs since it is not needed for FileEditAction
                        other_kwargs.pop('view_range')
                    if 'concise' in other_kwargs:
                        # Remove concise from other_kwargs since it is not needed for FileEditAction
                        other_kwargs.pop('concise')

                    # Filter out unexpected arguments
                    valid_kwargs = {}
                    # Get valid parameters from the str_replace_editor tool definition
                    str_replace_editor_tool = create_str_replace_editor_tool()
                    valid_params = set(
                        str_replace_editor_tool['function']['parameters'][
                            'properties'
                        ].keys()
                    )
                    for key, value in other_kwargs.items():
                        if key in valid_params:
                            valid_kwargs[key] = value
                        else:
                            raise FunctionCallValidationError(
                                f'Unexpected argument {key} in tool call {tool_call.function.name}. Allowed arguments are: {valid_params}'
                            )

                    action = FileEditAction(
                        path=path,
                        command=command,
                        impl_source=FileEditSource.OH_ACI,
                        **valid_kwargs,
                    )
            # ================================================
            # AgentThinkAction
            # ================================================
            elif tool_call.function.name == ThinkTool['function']['name']:
                action = AgentThinkAction(thought=arguments.get('thought', ''))

            # ================================================
            # BrowserTool
            # ================================================
            elif tool_call.function.name == BrowserTool['function']['name']:
                if 'code' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "code" in tool call {tool_call.function.name}'
                    )
                action = BrowseInteractiveAction(browser_actions=arguments['code'])

            # ================================================
            # WebReadTool (simplified browsing)
            # ================================================
            elif tool_call.function.name == WebReadTool['function']['name']:
                if 'url' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "url" in tool call {tool_call.function.name}'
                    )
                action = BrowseURLAction(url=arguments['url'])

            # ================================================
            # MCPAction (MCP)
            # ================================================
            elif mcp_tool_names and tool_call.function.name in mcp_tool_names:
                action = MCPAction(
                    name=tool_call.function.name,
                    arguments=arguments,
                )
            else:
                raise FunctionCallNotExistsError(
                    f'Tool {tool_call.function.name} is not registered. (arguments: {arguments}). Please check the tool name and retry with an existing tool.'
                )

            # We only add thought to the first action
            if i == 0:
                action = combine_thought(action, thought)
            # Add metadata for tool calling
            action.tool_call_metadata = ToolCallMetadata(
                tool_call_id=tool_call.id,
                function_name=tool_call.function.name,
                model_response=response,
                total_calls_in_response=len(assistant_msg.tool_calls),
            )
            actions.append(action)
    else:
        actions.append(
            MessageAction(
                content=str(assistant_msg.content) if assistant_msg.content else '',
                wait_for_response=True,
            )
        )

    # Add response id to actions
    # This will ensure we can match both actions without tool calls (e.g. MessageAction)
    # and actions with tool calls (e.g. CmdRunAction, IPythonRunCellAction, etc.)
    # with the token usage data
    for action in actions:
        action.response_id = response.id

    assert len(actions) >= 1
    return actions
