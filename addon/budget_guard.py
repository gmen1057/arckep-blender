# SPDX-License-Identifier: GPL-3.0-or-later
"""Local RUB budget guard for ArcKep 3D Blender add-on and MCP tools.

Implements the three user-selectable spending protection modes:
- ``threshold`` (default 150.0 RUB): auto-allows jobs up to ``max_auto_cost_rub``;
  jobs above the threshold require an explicit ``confirmed_max_cost_rub >= cost_rub``.
- ``confirm_always``: requires explicit ``confirmed_max_cost_rub >= cost_rub`` for any
  paid call (``cost_rub > 0``); free 0 RUB operations pass immediately.
- ``unlimited``: no local cap; passes through explicit ``confirmed_max_cost_rub`` if
  supplied, otherwise ``None`` (bounded only by account balance on the server).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

BUDGET_MODE_THRESHOLD = "threshold"
BUDGET_MODE_CONFIRM_ALWAYS = "confirm_always"
BUDGET_MODE_UNLIMITED = "unlimited"
BUDGET_MODES: tuple[str, ...] = (
    BUDGET_MODE_THRESHOLD,
    BUDGET_MODE_CONFIRM_ALWAYS,
    BUDGET_MODE_UNLIMITED,
)

DEFAULT_MAX_AUTO_COST_RUB = 150.0


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of checking an estimated RUB job price against user preferences."""

    allowed: bool
    requires_confirmation: bool
    effective_max_cost_rub: float | None
    message: str


def _parse_non_negative_decimal(value: float | int | str | Decimal, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative number, got bool")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a valid decimal amount: {value!r}") from exc
    if not parsed.is_finite() or parsed < Decimal("0"):
        raise ValueError(f"{field_name} must be finite and >= 0, got {value!r}")
    return parsed


def evaluate_budget(
    cost_rub: float | int | str | Decimal,
    mode: str = BUDGET_MODE_THRESHOLD,
    max_auto_cost_rub: float | int | str | Decimal = DEFAULT_MAX_AUTO_COST_RUB,
    confirmed_max_cost_rub: float | int | str | Decimal | None = None,
) -> BudgetDecision:
    """Evaluate whether a job costing ``cost_rub`` RUB may proceed immediately."""
    if mode not in BUDGET_MODES:
        raise ValueError(f"Unsupported budget mode {mode!r}; expected one of {BUDGET_MODES}")

    cost = _parse_non_negative_decimal(cost_rub, "cost_rub")
    confirmed = (
        _parse_non_negative_decimal(confirmed_max_cost_rub, "confirmed_max_cost_rub")
        if confirmed_max_cost_rub is not None
        else None
    )

    if mode == BUDGET_MODE_UNLIMITED:
        effective = float(confirmed) if confirmed is not None else None
        return BudgetDecision(
            allowed=True,
            requires_confirmation=False,
            effective_max_cost_rub=effective,
            message=f"Allowed in unlimited mode (estimated {cost:.2f} RUB).",
        )

    if mode == BUDGET_MODE_CONFIRM_ALWAYS:
        if cost == Decimal("0"):
            effective = float(confirmed) if confirmed is not None else 0.0
            return BudgetDecision(
                allowed=True,
                requires_confirmation=False,
                effective_max_cost_rub=effective,
                message="Free operation (0.00 RUB) allowed without confirmation.",
            )
        if confirmed is not None and confirmed >= cost:
            return BudgetDecision(
                allowed=True,
                requires_confirmation=False,
                effective_max_cost_rub=float(confirmed),
                message=f"Confirmed {cost:.2f} RUB within confirmed cap {confirmed:.2f} RUB.",
            )
        return BudgetDecision(
            allowed=False,
            requires_confirmation=True,
            effective_max_cost_rub=None,
            message=(
                f"Confirmation required: estimated cost is {cost:.2f} RUB "
                f"(mode='confirm_always'; pass confirmed_max_cost_rub >= {cost:.2f})."
            ),
        )

    # mode == "threshold"
    cap = _parse_non_negative_decimal(max_auto_cost_rub, "max_auto_cost_rub")
    if confirmed is not None:
        if confirmed >= cost:
            return BudgetDecision(
                allowed=True,
                requires_confirmation=False,
                effective_max_cost_rub=float(confirmed),
                message=f"Confirmed {cost:.2f} RUB within confirmed cap {confirmed:.2f} RUB.",
            )
        return BudgetDecision(
            allowed=False,
            requires_confirmation=True,
            effective_max_cost_rub=None,
            message=(
                f"Confirmation required: estimated cost {cost:.2f} RUB exceeds "
                f"confirmed_max_cost_rub {confirmed:.2f} RUB."
            ),
        )

    if cost <= cap:
        return BudgetDecision(
            allowed=True,
            requires_confirmation=False,
            effective_max_cost_rub=float(cap),
            message=f"Auto-approved {cost:.2f} RUB (within {cap:.2f} RUB threshold).",
        )

    return BudgetDecision(
        allowed=False,
        requires_confirmation=True,
        effective_max_cost_rub=None,
        message=(
            f"Confirmation required: estimated cost {cost:.2f} RUB exceeds "
            f"auto-approve threshold {cap:.2f} RUB (pass confirmed_max_cost_rub >= {cost:.2f})."
        ),
    )


def round_rub(value: float | int | str | Decimal) -> float:
    """Round a Blender C ``float`` or string RUB value to 2 kopek decimal places."""
    return round(float(value), 2)


def evaluate_prefs_budget(
    cost_rub: str | float, prefs: object, confirmed_prop: float
) -> BudgetDecision:
    """Evaluate ``cost_rub`` against ``AddonPreferences`` and a ``FloatProperty`` confirmation."""
    confirmed = round_rub(confirmed_prop) if float(confirmed_prop) >= 0.0 else None
    return evaluate_budget(
        cost_rub,
        mode=str(getattr(prefs, "budget_mode", BUDGET_MODE_THRESHOLD)),
        max_auto_cost_rub=round_rub(getattr(prefs, "max_auto_cost_rub", DEFAULT_MAX_AUTO_COST_RUB)),
        confirmed_max_cost_rub=confirmed,
    )
