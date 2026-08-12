from __future__ import annotations

from rca.analysis import two_proportion_test


def test_two_proportion_test_reports_signed_difference_and_interval() -> None:
    result = two_proportion_test(before_bookings=160, before_n=1_000, after_bookings=80, after_n=1_000)
    assert result["difference"] < 0
    assert result["ci_high"] < 0
    assert result["p_value"] < 0.001

