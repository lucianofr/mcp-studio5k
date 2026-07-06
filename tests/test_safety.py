import pytest

from mcp_studio5k.safety import (
    RateLimitError,
    SafetyError,
    WriteRateLimiter,
    check_allowed_property,
    check_safety_exclusions,
)

L5X_TOUCHING_ESTOP = """<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0">
  <Controller>
    <Routines>
      <Routine Name="R1" Type="ST">
        <STContent>
          <Line Number="0"><![CDATA[ESTOP_OK := Door_Closed;]]></Line>
        </STContent>
      </Routine>
    </Routines>
    <Tags>
      <Tag Name="ESTOP_OK" DataType="BOOL"/>
      <Tag Name="Door_Closed" DataType="BOOL"/>
    </Tags>
  </Controller>
</RSLogix5000Content>
"""


def test_returns_excluded_tag_names_present_in_content():
    exclusions = frozenset({"ESTOP_OK", "Safety_Reset"})
    touched = check_safety_exclusions(
        L5X_TOUCHING_ESTOP, exclusions, max_bytes=5_000_000
    )
    assert touched == ("ESTOP_OK",)


def test_returns_empty_tuple_when_no_exclusion_referenced():
    exclusions = frozenset({"Safety_Reset"})
    touched = check_safety_exclusions(
        L5X_TOUCHING_ESTOP, exclusions, max_bytes=5_000_000
    )
    assert touched == ()


def test_raises_when_content_exceeds_max_bytes():
    with pytest.raises(SafetyError):
        check_safety_exclusions("x" * 100, frozenset({"A"}), max_bytes=10)


def test_rejects_doctype_declaration():
    doctype = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY x "y">]>'
        "<RSLogix5000Content><Controller/></RSLogix5000Content>"
    )
    with pytest.raises(SafetyError):
        check_safety_exclusions(doctype, frozenset({"A"}), max_bytes=5_000_000)


def test_allowed_property_true_when_in_allowlist():
    assert check_allowed_property("Description", frozenset({"Description", "Name"})) is True


def test_allowed_property_false_when_absent_or_empty_allowlist():
    assert check_allowed_property("MajorRevision", frozenset({"Description"})) is False
    assert check_allowed_property("Description", frozenset()) is False


def test_rate_limiter_counts_writes():
    limiter = WriteRateLimiter(limit=5, cooldown_seconds=30.0)
    limiter.record_write(now=100.0)
    limiter.record_write(now=101.0)
    assert limiter.count == 2


def test_needs_reconfirm_once_count_reaches_limit():
    limiter = WriteRateLimiter(limit=3, cooldown_seconds=30.0)
    for t in (1.0, 2.0):
        limiter.record_write(now=t)
    assert limiter.needs_reconfirm() is False
    limiter.record_write(now=3.0)
    assert limiter.needs_reconfirm() is True


def test_in_cooldown_false_before_any_write():
    limiter = WriteRateLimiter(limit=5, cooldown_seconds=30.0)
    assert limiter.in_cooldown(now=0.0) is False


def test_in_cooldown_true_within_window_false_after():
    limiter = WriteRateLimiter(limit=5, cooldown_seconds=30.0)
    limiter.record_write(now=100.0)
    assert limiter.in_cooldown(now=120.0) is True
    assert limiter.in_cooldown(now=130.0) is False
    assert limiter.in_cooldown(now=131.0) is False


def test_check_does_not_consume_budget():
    # check() only validates; budget is consumed by an explicit record_write.
    limiter = WriteRateLimiter(limit=3, cooldown_seconds=10.0)
    limiter.check(now=100.0)
    limiter.check(now=100.0)
    assert limiter.count == 0


def test_check_raises_when_in_cooldown():
    limiter = WriteRateLimiter(limit=3, cooldown_seconds=10.0)
    limiter.check(now=100.0)
    limiter.record_write(now=100.0)
    with pytest.raises(RateLimitError, match="cooldown"):
        limiter.check(now=105.0)


def test_check_raises_when_limit_reached():
    limiter = WriteRateLimiter(limit=2, cooldown_seconds=0.0)
    for t in (1.0, 2.0):
        limiter.check(now=t)
        limiter.record_write(now=t)
    with pytest.raises(RateLimitError, match="limit"):
        limiter.check(now=3.0)


def test_reset_clears_budget_after_limit_reached():
    limiter = WriteRateLimiter(limit=2, cooldown_seconds=0.0)
    for t in (1.0, 2.0):
        limiter.check(now=t)
        limiter.record_write(now=t)
    limiter.reset()
    assert limiter.count == 0
    # Fresh budget after reset: two more writes allowed, third refused again.
    for t in (3.0, 4.0):
        limiter.check(now=t)
        limiter.record_write(now=t)
    with pytest.raises(RateLimitError, match="limit"):
        limiter.check(now=5.0)
