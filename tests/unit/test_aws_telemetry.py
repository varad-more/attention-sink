"""Structured logging, and the one thing it must never do.

The redaction test is the point of the module. Everything else about a log line is a
convenience; a log line carrying a prompt, a memory, or a token is a leak of the
experiment through the least access-controlled artefact a deployment produces.
"""

from __future__ import annotations

import io
import json

from attention_sink.aws.telemetry import FIELDS, StructuredLogger, log_fields


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_a_line_is_one_json_object_carrying_the_bound_context():
    stream = io.StringIO()
    logger = StructuredLogger(service="run-cycle", environment="staging", stream=stream)
    logger.bind(run_id="run_x", cycle=3).info("cycle.committed", arm_id="arm_fifo")
    (line,) = _lines(stream)
    assert line["service"] == "run-cycle"
    assert line["environment"] == "staging"
    assert line["run_id"] == "run_x"
    assert line["cycle"] == 3
    assert line["arm_id"] == "arm_fifo"
    assert line["event"] == "cycle.committed"


def test_a_field_outside_the_closed_set_is_dropped():
    """An open field set means a dashboard column that exists only sometimes."""
    assert "journal_entry" not in log_fields(journal_entry="I remember the lighthouse")
    assert "candidate_memory" not in log_fields(candidate_memory="a memory")


def test_nothing_that_could_carry_a_secret_or_a_generation_can_be_logged():
    """The allowlist is the whole mechanism, so this is the whole guarantee."""
    for name in (
        "lock_token",
        "authorization",
        "password",
        "prompt",
        "session_token",
        "journal_entry",
        "candidate_memory",
        "stimulus",
        "answer",
    ):
        assert log_fields(**{name: "value"}) == {}
        assert name not in FIELDS


def test_a_token_count_is_carried_because_it_is_a_count():
    """The two fields that contain the word "token" are integers, not credentials."""
    fields = log_fields(input_tokens=1200, output_tokens=340)
    assert fields == {"input_tokens": 1200, "output_tokens": 340}


def test_an_unset_field_is_omitted_rather_than_written_as_null():
    assert log_fields(run_id="run_x", cycle=None, arm_id=None) == {"run_id": "run_x"}


def test_fields_are_written_in_the_declared_order():
    stream = io.StringIO()
    logger = StructuredLogger(service="analysis", environment="staging", stream=stream)
    logger.error("analysis.failed", result_code="analysis_failed", cycle=4, run_id="run_x")
    (line,) = _lines(stream)
    written = [key for key in line if key in FIELDS]
    assert written == [key for key in FIELDS if key in line]
    assert line["level"] == "ERROR"


def test_binding_never_mutates_the_logger_it_came_from():
    stream = io.StringIO()
    base = StructuredLogger(service="read-api", environment="local", stream=stream)
    base.bind(run_id="run_y").info("request")
    base.warning("second")
    first, second = _lines(stream)
    assert first["run_id"] == "run_y"
    assert "run_id" not in second
