from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def contract_contains(shape: str, lower: float | None, upper: float | None, value_f: float | None) -> bool | None:
    if value_f is None:
        return None
    shape = (shape or "").lower()
    if shape == "below":
        return None if upper is None else float(value_f) <= float(upper)
    if shape == "above":
        return None if lower is None else float(value_f) >= float(lower)
    if shape == "bucket":
        return None if lower is None or upper is None else float(lower) <= float(value_f) <= float(upper)
    return None


def boundary_distance_f(shape: str, lower: float | None, upper: float | None, value_f: float | None) -> float | None:
    if value_f is None:
        return None
    candidates = [float(x) for x in (lower, upper) if x is not None]
    if not candidates:
        return None
    return min(abs(float(value_f) - x) for x in candidates)


@dataclass(frozen=True)
class ContractFlip:
    contract_id: str
    official_winner: bool | None
    public_winner: bool | None
    reconstructed_winner: bool | None
    public_flip: bool | None
    reconstructed_flip: bool | None
    official_boundary_distance_f: float | None
    public_boundary_distance_f: float | None


def evaluate_contract_flip(
    contract: dict[str, Any],
    *,
    official_f: float | None,
    public_f: float | None,
    reconstructed_f: float | None,
) -> ContractFlip:
    shape = str(contract.get("shape") or "")
    lower = contract.get("lower")
    upper = contract.get("upper")
    official = contract_contains(shape, lower, upper, official_f)
    public = contract_contains(shape, lower, upper, public_f)
    reconstructed = contract_contains(shape, lower, upper, reconstructed_f)
    return ContractFlip(
        contract_id=str(contract.get("contract_id") or ""),
        official_winner=official,
        public_winner=public,
        reconstructed_winner=reconstructed,
        public_flip=None if official is None or public is None else official != public,
        reconstructed_flip=None if official is None or reconstructed is None else official != reconstructed,
        official_boundary_distance_f=boundary_distance_f(shape, lower, upper, official_f),
        public_boundary_distance_f=boundary_distance_f(shape, lower, upper, public_f),
    )
