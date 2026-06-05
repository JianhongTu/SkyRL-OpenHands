import json

from evaluation.benchmarks.swe_bench.r2e_utils import get_reward, parse_log_pytest


def test_parse_log_pytest_and_reward_match():
    log = """
=========================== short test summary info ============================
PASSED r2e_tests/test_example.py::test_add
FAILED r2e_tests/test_example.py::test_subtract - AssertionError
ERROR r2e_tests/test_example.py::test_import - ImportError
"""
    parsed = parse_log_pytest(log)
    assert parsed == {
        'test_add': 'PASSED',
        'test_subtract': 'FAILED',
        'test_import': 'ERROR',
    }

    instance = {
        'expected_output_json': json.dumps(
            {
                'test_add': 'PASSED',
                'test_subtract': 'FAILED',
                'test_import': 'ERROR',
            }
        )
    }
    assert get_reward(parsed, instance) == 1.0


def test_get_reward_mismatch_returns_zero():
    instance = {'expected_output_json': json.dumps({'test_add': 'PASSED'})}
    assert get_reward({'test_add': 'FAILED'}, instance) == 0.0


def test_get_reward_rejects_empty_parsed_test_name():
    instance = {'expected_output_json': json.dumps({'': 'ERROR'})}
    assert get_reward({'': 'ERROR'}, instance) == 0.0


def test_parse_log_pytest_keeps_module_error_name():
    log = """
=========================== short test summary info ============================
ERROR r2e_tests/test_import.py - ImportError
"""
    assert parse_log_pytest(log) == {'r2e_tests/test_import.py': 'ERROR'}


def test_parse_log_pytest_uses_status_prefix():
    log = """
=========================== short test summary info ============================
FAILED r2e_tests/test_example.py::test_status - AssertionError: expected PASSED
ERROR r2e_tests/test_example.py::test_import - expected FAILED
"""
    assert parse_log_pytest(log) == {
        'test_status': 'FAILED',
        'test_import': 'ERROR',
    }


def test_get_reward_handles_all_passing_pytest_without_summary():
    instance = {
        'expected_output_json': json.dumps(
            {
                'test_one': 'PASSED',
                'test_two': 'PASSED',
            }
        )
    }
    log = '======================== 2 passed in 0.12s ========================'
    assert get_reward({}, instance, log) == 1.0


def test_get_reward_all_passing_output_does_not_satisfy_expected_failure():
    instance = {'expected_output_json': json.dumps({'test_one': 'FAILED'})}
    log = '======================== 1 passed in 0.12s ========================'
    assert get_reward({}, instance, log) == 0.0


def test_get_reward_rejects_partial_all_passing_output():
    instance = {
        'expected_output_json': json.dumps(
            {
                'test_one': 'PASSED',
                'test_two': 'PASSED',
            }
        )
    }
    log = '======================== 1 passed in 0.12s ========================'
    assert get_reward({}, instance, log) == 0.0


def test_get_reward_rejects_skipped_all_passing_output():
    instance = {
        'expected_output_json': json.dumps(
            {
                'test_one': 'PASSED',
                'test_two': 'PASSED',
            }
        )
    }
    log = '================== 1 passed, 1 skipped in 0.12s =================='
    assert get_reward({}, instance, log) == 0.0
