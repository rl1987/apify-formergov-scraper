"""A fake Actor that charges the way the real one does.

Written from the SDK's own source (apify 4.0.2) rather than from its docs, because
the behaviour that matters here is not documented: `push_data` with a
`charged_event_name` WRITES first and charges second, both under the charge lock,
and it truncates the write to what the remaining budget covers. A fake that
charged first would make the tests pass on a leak.

Verified against the real `ChargingManagerImplementation` on 2026-10-29:

  on-platform, event in pricingInfos    charge(count=5) -> charged_count 5, API called
  on-platform, event NOT in pricingInfos charge(count=5) -> charged_count 5, NO API call
                                         and push_data does not truncate
  off-platform, event NOT priced         charge(count=5) -> charged_count 0, limit reached
  no pay-per-event pricing at all        charge(count=5) -> charged_count 0

The second line is why `Ledger` resolves event names from `per_event_prices`: on
the platform the SDK's own counters say "fully billed" for a name that bills $0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _add(self, level: str, message: str) -> None:
        self.lines.append((level, str(message)))

    def info(self, message: str) -> None:
        self._add("info", message)

    def warning(self, message: str) -> None:
        self._add("warning", message)

    def error(self, message: str) -> None:
        self._add("error", message)

    def exception(self, message: str) -> None:
        self._add("error", message)

    def text(self) -> str:
        return "\n".join(f"{level}: {message}" for level, message in self.lines)


@dataclass
class FakeChargeResult:
    event_charge_limit_reached: bool
    charged_count: int
    chargeable_within_limit: dict[str, int | None] = field(default_factory=dict)


class FakeChargingManager:
    def __init__(self, prices: dict[str, float], cap: float | None, *, is_ppe: bool = True) -> None:
        self.prices = {name: Decimal(str(price)) for name, price in prices.items()}
        self.cap = Decimal("Infinity") if cap is None else Decimal(str(cap))
        self.is_ppe = is_ppe
        self.counts: dict[str, int] = {}
        self.spent = Decimal(0)

    # -- the public ChargingManager surface the ledger uses ---------------

    def get_pricing_info(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            is_pay_per_event=self.is_ppe,
            per_event_prices=dict(self.prices),
            max_total_charge_usd=self.cap,
            pricing_model="PAY_PER_EVENT" if self.is_ppe else "FREE",
        )

    def get_charged_event_count(self, event_name: str) -> int:
        return self.counts.get(event_name, 0)

    def get_max_total_charge_usd(self) -> Decimal:
        return self.cap

    def calculate_total_charged_amount(self) -> Decimal:
        return self.spent

    def compute_push_data_limit(self, items_count: int, event_name: str, *, is_default_dataset: bool) -> int:
        price = self._price(event_name)
        if not price:
            return items_count
        room = (self.cap - self.spent) / price
        return min(items_count, max(0, math.floor(room)) if room.is_finite() else items_count)

    def is_event_charge_limit_reached(self, event_name: str) -> bool:
        limit = self._max_within_limit(event_name)
        return limit is not None and limit <= 0

    # -- internals -------------------------------------------------------

    def _price(self, event_name: str) -> Decimal:
        # On the platform an unpriced event costs 0, which is what makes it bill
        # nothing while reporting success.
        return self.prices.get(event_name, Decimal(0))

    def _max_within_limit(self, event_name: str) -> int | None:
        price = self._price(event_name)
        if not price:
            return None
        room = (self.cap - self.spent) / price
        return max(0, math.floor(room)) if room.is_finite() else None

    def charge(self, event_name: str, count: int) -> FakeChargeResult:
        if not self.is_ppe:
            return FakeChargeResult(event_charge_limit_reached=False, charged_count=0)
        if count <= 0:
            return FakeChargeResult(
                event_charge_limit_reached=self.is_event_charge_limit_reached(event_name),
                charged_count=0,
            )
        allowed = self._max_within_limit(event_name)
        charged = count if allowed is None else min(count, allowed)
        if charged == 0:
            return FakeChargeResult(event_charge_limit_reached=True, charged_count=0)
        self.counts[event_name] = self.counts.get(event_name, 0) + charged
        self.spent += charged * self._price(event_name)
        return FakeChargeResult(
            event_charge_limit_reached=self.is_event_charge_limit_reached(event_name),
            charged_count=charged,
        )


class FakeActor:
    """Stands in for `apify.Actor`. Inject with `monkeypatch.setattr(billing, "Actor", ...)`."""

    def __init__(self, prices: dict[str, float] | None = None, cap: float | None = None, *, is_ppe: bool = True) -> None:
        self.log = _Log()
        self.dataset: list[dict] = []
        self.values: dict[str, Any] = {}
        self.status_messages: list[str] = []
        self.manager = FakeChargingManager(prices or {}, cap, is_ppe=is_ppe)
        self.fail_write_times = 0
        self.fail_charge_after_write_times = 0
        self.fail_charge_times = 0
        self.pricing_read_raises = False

    def get_charging_manager(self) -> Any:
        if self.pricing_read_raises:
            raise RuntimeError("charging manager not available")
        return self.manager

    async def charge(self, *, event_name: str, count: int = 1) -> FakeChargeResult:
        """Keyword-only, exactly like SDK 4 - a positional call must fail here too."""
        if self.fail_charge_times:
            self.fail_charge_times -= 1
            raise RuntimeError("charge API unavailable")
        return self.manager.charge(event_name, count)

    async def push_data(self, data: list[dict] | dict, *, charged_event_name: str | None = None) -> FakeChargeResult:
        if charged_event_name and charged_event_name.startswith("apify-"):
            raise ValueError(f'Cannot charge for synthetic event "{charged_event_name}" manually')
        rows = data if isinstance(data, list) else [data]
        if not rows:
            return FakeChargeResult(event_charge_limit_reached=False, charged_count=0)
        if self.fail_write_times:
            self.fail_write_times -= 1
            raise RuntimeError("dataset write failed")
        if charged_event_name is None:
            self.dataset.extend(rows)
            return FakeChargeResult(event_charge_limit_reached=False, charged_count=0)
        limit = self.manager.compute_push_data_limit(
            items_count=len(rows), event_name=charged_event_name, is_default_dataset=True
        )
        self.dataset.extend(rows[:limit])
        if limit and self.manager.prices.get("apify-default-dataset-item"):
            # The platform charges the synthetic event per dataset write by itself, with
            # no charge call from the Actor - but it DOES land in the SDK's own running
            # total, which is what the audit reconciles against.
            self.manager.charge("apify-default-dataset-item", limit)
        if self.fail_charge_after_write_times:
            # The write landed and the charge did not. The SDK increments its own
            # counter before the request, so the ledger can tell this apart.
            self.fail_charge_after_write_times -= 1
            self.manager.counts[charged_event_name] = (
                self.manager.counts.get(charged_event_name, 0) + limit
            )
            raise RuntimeError("charge API unavailable")
        return self.manager.charge(charged_event_name, limit)

    async def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value

    async def set_status_message(self, message: str) -> None:
        self.status_messages.append(message)
