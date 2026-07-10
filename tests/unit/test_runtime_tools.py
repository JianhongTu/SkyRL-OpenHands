import argparse
import sys

import pytest

from openhands.runtime.tools import search as search_tool
from openhands.runtime.tools import str_replace_editor as editor_tool


def test_search_cli_flattens_line_number_groups(monkeypatch, capsys):
    captured = {}

    def search_code_snippets(**kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(search_tool, 'search_code_snippets', search_code_snippets)
    monkeypatch.setattr(
        sys,
        'argv',
        ['search', '--file_path_or_pattern', 'example.py', '--line_nums', '[1, 2]'],
    )

    search_tool.main()

    assert captured['line_nums'] == [1, 2]
    assert capsys.readouterr().out.strip() == 'ok'


def test_search_cli_treats_empty_line_numbers_as_absent(monkeypatch, capsys):
    captured = {}

    def search_code_snippets(**kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(search_tool, 'search_code_snippets', search_code_snippets)
    monkeypatch.setattr(
        sys,
        'argv',
        ['search', '--file_path_or_pattern', 'example.py', '--line_nums', '[]'],
    )

    search_tool.main()

    assert captured['line_nums'] is None
    assert capsys.readouterr().out.strip() == 'ok'


def test_editor_reads_non_python_files_by_default(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('enabled = true\n')

    result = editor_tool.StrReplaceEditor({}).view(path)

    assert result.error == ''
    assert 'enabled = true' in result.output


def test_editor_uses_original_lines_when_range_and_concise_are_requested(tmp_path):
    path = tmp_path / 'large.py'
    path.write_text(
        'def example():\n'
        + ''.join(f'    value_{line} = {line}\n' for line in range(1, 200))
    )

    result = editor_tool.StrReplaceEditor({}).view(
        path, view_range=[150, 160], concise=True
    )

    assert result.error == ''
    assert '   150     value_149 = 149' in result.output
    assert '   160     value_159 = 159' in result.output


@pytest.mark.parametrize(
    ('value', 'expected'),
    [('true', True), ('1', True), ('false', False), ('0', False)],
)
def test_editor_parse_bool(value, expected):
    assert editor_tool.parse_bool(value) is expected


def test_editor_parse_bool_rejects_unknown_values():
    with pytest.raises(argparse.ArgumentTypeError):
        editor_tool.parse_bool('sometimes')
