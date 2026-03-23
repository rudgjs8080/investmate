"""숫자 포맷 유틸리티 — 리포트 모듈 공통."""

from __future__ import annotations


def fmt_large_number(n: float | int | None) -> str:
    """큰 숫자를 읽기 쉬운 형태로 변환한다.

    Examples:
        1_500_000_000_000 → "1.5T"
        2_300_000_000 → "2.3B"
        45_000_000 → "45.0M"
        1_200_000 → "1.2M"
        500_000 → "500.0K"
        1234 → "1,234"
    """
    if n is None:
        return "-"
    n = float(n)
    abs_n = abs(n)
    sign = "-" if n < 0 else ""
    if abs_n >= 1_000_000_000_000:
        return f"{sign}{abs_n / 1_000_000_000_000:.1f}T"
    if abs_n >= 1_000_000_000:
        return f"{sign}{abs_n / 1_000_000_000:.1f}B"
    if abs_n >= 1_000_000:
        return f"{sign}{abs_n / 1_000_000:.1f}M"
    if abs_n >= 1_000:
        return f"{sign}{abs_n / 1_000:.1f}K"
    return f"{n:,.0f}"
