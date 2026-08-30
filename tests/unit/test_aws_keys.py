"""The single-table key layout.

Worth testing directly because a wrong sort key does not raise: it returns nothing,
and a query that returns nothing looks exactly like a run that has not got there yet.
"""

from __future__ import annotations

from attention_sink.aws import keys


def test_a_run_partition_holds_everything_about_one_run():
    partition = keys.run_pk("run_alpha")
    assert partition == "RUN#run_alpha"


def test_cycles_are_padded_so_ten_sorts_after_nine():
    """The failure this padding prevents, stated as the assertion.

    DynamoDB sorts sort keys as bytes. Unpadded, `CYCLE#10` sorts before `CYCLE#9`
    and a range query over the middle of a run silently returns the wrong window.
    """
    assert keys.snapshot_sk(9, "arm_fifo") < keys.snapshot_sk(10, "arm_fifo")
    assert keys.prepared_sk(2) < keys.prepared_sk(11)
    assert keys.interview_sk(0, "arm_lru") < keys.interview_sk(12, "arm_lru")


def test_every_record_type_has_a_distinct_prefix():
    """No prefix may be a prefix of another, or one query would return two kinds."""
    sort_keys = {
        "state": keys.arm_state_sk("arm_fifo"),
        "prepared": keys.prepared_sk(1),
        "snapshot": keys.snapshot_sk(1, "arm_fifo"),
        "interview": keys.interview_sk(1, "arm_fifo"),
        "metric": keys.metric_sk("origin_recall", 1, "arm_fifo"),
        "analysis": keys.analysis_sk(1),
        "status": keys.analysis_status_sk("all"),
        "artifact": keys.artifact_sk("divergence"),
        "export": keys.export_sk("export-1"),
    }
    assert len(set(sort_keys.values())) == len(sort_keys)
    assert not keys.analysis_status_sk("all").startswith(keys.analysis_sk(1))


def test_the_analysis_summary_sorts_clear_of_every_per_cycle_marker():
    """A prefix query for the markers must not pick up the run-wide summary."""
    assert keys.analysis_status_sk("all") > keys.analysis_sk(999999)


def test_a_snapshot_sort_key_gives_its_cycle_back():
    assert keys.cycle_of_snapshot_sk(keys.snapshot_sk(17, "arm_sink")) == 17
    assert keys.cycle_of_snapshot_sk(keys.prepared_sk(17)) is None
    assert keys.cycle_of_snapshot_sk(keys.interview_sk(17, "arm_sink")) is None


def test_a_metric_without_an_arm_is_recorded_against_the_whole_run():
    assert keys.metric_sk("divergence_mean", 12, None).endswith(f"#ARM#{keys.CROSS_ARM}")
    assert keys.metric_sk("divergence_mean", 12, "arm_fifo").endswith("#ARM#arm_fifo")


def test_a_metric_prefix_narrows_only_as_far_as_the_sort_key_allows():
    """Name then cycle, because that is the order the key puts them in.

    Narrowing by cycle without a name cannot be a prefix, so the prefix widens back
    to every metric and the adapter filters -- which is correct, and is why the
    adapter also filters on cycle after the query.
    """
    assert keys.metric_prefix() == "METRIC#"
    assert keys.metric_prefix("origin_recall") == "METRIC#origin_recall#"
    assert keys.metric_prefix("origin_recall", 3).startswith("METRIC#origin_recall#CYCLE#")
    assert keys.metric_prefix(None, 3) == "METRIC#"


def test_a_cache_partition_changes_with_the_model():
    """Two models must never share a vector or a count."""
    assert keys.embedding_pk("amazon.titan-embed-text-v2:0") != keys.embedding_pk(
        "amazon.titan-embed-text-v1"
    )
    assert keys.token_pk("bedrock-count-tokens-v1") != keys.token_pk("heuristic-v1")


def test_a_model_hash_carries_no_punctuation_from_the_identifier():
    """A key that reads like a path invites somebody to parse it back out."""
    hashed = keys.model_hash("us.anthropic.some-model-v1:0")
    assert hashed.isalnum()
    assert len(hashed) == 32
