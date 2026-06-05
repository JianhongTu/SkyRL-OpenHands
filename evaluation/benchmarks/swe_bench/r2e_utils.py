import json
import re
from typing import Any


def parse_log_pytest(log: str | None) -> dict[str, str]:
    if log is None or 'short test summary info' not in log:
        return {}

    test_status_map: dict[str, str] = {}
    summary = log.split('short test summary info', 1)[1].strip()
    for line in summary.splitlines():
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        status = parts[0]
        if status not in {'PASSED', 'FAILED', 'ERROR'}:
            continue

        details = parts[1] if len(parts) > 1 else ''
        if '::' in details:
            test_name = '.'.join(details.split('::')[1:])
        else:
            test_name = details
        test_name = test_name.split(' - ')[0]
        test_status_map[test_name] = status
    return test_status_map


def _decolor_dict_keys(values: dict[str, str]) -> dict[str, str]:
    return {re.sub(r'\u001b\[\d+m', '', key): value for key, value in values.items()}


def _pytest_all_passed(log: str | None, expected_count: int) -> bool:
    if log is None:
        return False
    clean_log = re.sub(r'\x1b\[[0-9;]*m|\r', '', log)
    summary_matches = re.findall(r'=+\s+(.+?)\s+=+', clean_log)
    if not summary_matches:
        return False

    summary = summary_matches[-1]
    counts = {
        status: int(count)
        for count, status in re.findall(r'(\d+)\s+([a-zA-Z_]+)', summary)
    }
    return (
        counts.get('passed') == expected_count
        and all(
            counts.get(status, 0) == 0
            for status in ('failed', 'error', 'errors', 'skipped', 'xfailed', 'xpassed')
        )
    )


def get_reward(parsed: dict[str, str], instance: Any, log: str | None = None) -> float:
    parsed = _decolor_dict_keys(parsed)
    expected_json = instance['expected_output_json']
    expected = _decolor_dict_keys(json.loads(expected_json))

    parsed = {key.split(' - ')[0]: parsed[key] for key in sorted(parsed.keys())}
    expected = {
        key.split(' - ')[0]: expected[key] for key in sorted(expected.keys())
    }

    if not parsed and expected:
        if _pytest_all_passed(log, len(expected)) and all(
            value == 'PASSED' for value in expected.values()
        ):
            return 1.0
        return 0.0

    if len(parsed) != len(expected):
        return 0.0

    for key, value in parsed.items():
        if not key:
            return 0.0
        if key not in expected or value != expected[key]:
            return 0.0
    if any(not key for key in expected):
        return 0.0
    return 1.0
