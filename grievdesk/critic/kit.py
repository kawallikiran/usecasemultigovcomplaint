"""Interface each use case implements so the generic critic runner can test it."""
from dataclasses import dataclass
from typing import Callable


@dataclass
class Probe:
    test_no: int                 # which of the 8 critic tests
    name: str
    case: dict
    expected: str                # human-readable expectation
    check: Callable              # check(output) -> (passed: bool, actual: str)
    severity: str = "High"       # severity if it fails


class CriticKit:
    """Override these in each use case."""
    system_name = "system"
    subgroup_fields: list = []
    missed_adverse_name = "Cases that needed escalation but were not escalated"

    def __init__(self, system, cfg):
        self.system = system
        self.cfg = cfg

    def cases(self) -> list: raise NotImplementedError
    def case_id(self, case) -> str: return str(case.get("id"))
    def expected_label(self, case) -> str: raise NotImplementedError
    def label(self, out) -> str: raise NotImplementedError
    def is_adverse(self, out) -> bool: raise NotImplementedError
    def expected_adverse(self, case) -> bool: raise NotImplementedError
    def perturbations(self) -> list: return []          # [(name, fn(case) -> case)]
    def robustness_targets(self, cases) -> list: return cases
    def acceptable_shift(self, base_out, new_out) -> bool: return self.label(base_out) == self.label(new_out)
    def hallucination_errors(self, case, out) -> list: return []
    def probes(self) -> list: return []                  # list[Probe]
