"""OCC 21-character option symbols.

`SPY   260818C00604000` = root padded to 6, YYMMDD, C/P, strike x 1000 in 8 digits.
Getting the padding wrong produces a symbol the API rejects; getting the strike
scaling wrong produces a symbol that is *valid* and is the wrong contract.
"""

from __future__ import annotations

from datetime import date

from .models import Right


def build(underlying: str, expiry: date, right: Right, strike: float) -> str:
    if strike <= 0:
        raise ValueError(f"strike must be positive, got {strike}")
    thousandths = round(strike * 1000)
    if abs(thousandths - strike * 1000) > 1e-6:
        raise ValueError(f"strike {strike} is not representable in OCC thousandths")
    return f"{underlying.upper():<6}{expiry:%y%m%d}{right.value}{thousandths:08d}".replace(" ", "")


def parse(symbol: str) -> tuple[str, date, Right, float]:
    """Split an OCC symbol. The fixed-width tail is parsed from the right so that
    roots of any length (SPY, SPXW, BRK.B) work without a padding assumption."""
    tail = symbol[-15:]
    root = symbol[: len(symbol) - 15].strip()
    if len(tail) != 15 or not root:
        raise ValueError(f"not an OCC option symbol: {symbol!r}")
    expiry = date(2000 + int(tail[0:2]), int(tail[2:4]), int(tail[4:6]))
    right = Right(tail[6])
    strike = int(tail[7:]) / 1000.0
    return root, expiry, right, strike
