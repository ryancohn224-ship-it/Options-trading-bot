"""OCC option symbol utilities. Alpaca uses OCC format: ROOT + YYMMDD + C/P + strike*1000 (8 digits)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_RE = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


@dataclass(frozen=True)
class OptionKey:
    root: str
    expiry: date
    is_call: bool
    strike: float

    @property
    def symbol(self) -> str:
        return format_occ(self.root, self.expiry, self.is_call, self.strike)


def parse_occ(sym: str) -> OptionKey:
    m = _RE.match(sym.strip().upper())
    if not m:
        raise ValueError(f"not an OCC symbol: {sym}")
    root, yy, mm, dd, cp, k = m.groups()
    return OptionKey(root, date(2000 + int(yy), int(mm), int(dd)), cp == "C", int(k) / 1000.0)


def format_occ(root: str, expiry: date, is_call: bool, strike: float) -> str:
    return f"{root.upper()}{expiry:%y%m%d}{'C' if is_call else 'P'}{int(round(strike * 1000)):08d}"
