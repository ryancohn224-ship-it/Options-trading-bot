from __future__ import annotations

from ..config import Cfg
from .f0_cost import CostFactor
from .f1_vrp import VRPFactor
from .f2_skew import SkewFactor
from .f7_trend import TrendFactor


def default_factors(cfg: Cfg):
    return [VRPFactor(cfg), SkewFactor(cfg), TrendFactor(cfg), CostFactor(cfg)]
