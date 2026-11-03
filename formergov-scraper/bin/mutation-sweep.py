#!/usr/bin/env python3
"""Does tests/test_billing.py actually bite?

Breaks one rule of src/billing.py at a time and runs the suite. A mutant the
suite does not catch is a rule nothing is guarding, and every hole this found the
first time was real: the slice-to-`charged_count` was only ever exercised through
the batch planner, the unpriced-slice gate only matters on a run that bills
nothing, and the cap message could only be proved right on a run that ALSO had
duplicates to over-count. 25 of 25 caught on 2026-10-29.

One mutation was retired rather than fixed, and the reason is a property of the
design worth keeping written down: discarding the rows `_plan` cut ("deliver rows
the plan cut away") changes nothing, because the write truncates to the same
limit by itself AND the slice budget is whatever is left after reserving those
rows - so whenever the base limit binds, there is no slice money left to
mis-spend on a cut row. The trim stays as defence in depth; it is not what makes
the slice path safe.

    python3 bin/mutation-sweep.py

The module is copied aside and restored at the end - never `git checkout`, which
would take your uncommitted work with it. A mutant that hangs the suite counts as
caught rather than stalling the sweep.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "my_actor/billing.py"

# (what the mutation breaks, the exact text to replace, what to replace it with)
MUTANTS: list[tuple[str, str, str]] = [
    ("push then charge, instead of the atomic form",
     "                return await Actor.push_data(rows, charged_event_name=event)",
     "                await Actor.push_data(rows)\n"
     "                return await Actor.charge(event_name=event, count=len(rows))"),
    ("deliver everything, not just what was charged",
     "        return rows[: result.charged_count], result.charged_count",
     "        return rows, result.charged_count"),
    ("gate on the forward-looking flag instead of charged_count",
     "        return rows[: result.charged_count], result.charged_count",
     "        return ([] if result.event_charge_limit_reached else rows), result.charged_count"),
    ("no dedupe",
     "                        if marker in self._seen:",
     "                        if False:"),
    ("bill rows that resolved nothing",
     "                if self._resolved_pred is not None and not self._resolved_pred(kind, row):",
     "                if False:"),
    ("no dataset-schema guard",
     "                reason = self._guard.reject_reason(row)",
     "                reason = None"),
    ("deliver a row the dataset would reject",
     '                counts["invalid"] += 1',
     '                counts["invalid"] += 1\n                deliverable.append(row)'),
    ("no budget planning before the slice charges",
     "        base_event = self.event_for(kind)\n        if base_event is None or not rows:\n"
     "            return rows, 0, None",
     "        base_event = self.event_for(kind)\n        if True:\n            return rows, 0, None"),
    ("let the slices spend the money the write needs",
     "            slice_budget = remaining - keep * per_row",
     "            slice_budget = remaining"),
    ("charge a slice for every row that carries it, budget or not",
     "            if budget is not None and price:\n                wanted = min(wanted, int(budget // price))",
     "            if False:\n                wanted = min(wanted, int(budget // price))"),
    ("a refused surcharge withholds the row",
     "            for surcharge in surcharges:\n"
     "                await self._charge_surcharge(surcharge, len(delivered))",
     "            for surcharge in surcharges:\n"
     "                _event = self.event_for(surcharge) or 'x'\n"
     "                if await self._charge(_event, len(delivered), role='surcharge') < len(delivered):\n"
     "                    return Delivery(kind=kind, delivered=[], billed=0, stopped=self._stop_reason, **counts)"),
    ("strip a slice even when its event is unpriced",
     "            event = self.event_for(part.kind)\n            if event is None:\n                continue",
     "            event = self.event_for(part.kind) or 'x'\n            if False:\n                continue"),
    ("charge positionally",
     "            result = await Actor.charge(event_name=event, count=count)",
     "            result = await Actor.charge(event, count)"),
    ("never stop on the cap",
     "        if result.event_charge_limit_reached:\n            self._stop(STOP_CAP)\n"
     "        return rows[: result.charged_count]",
     "        if False:\n            self._stop(STOP_CAP)\n        return rows[: result.charged_count]"),
    ("do not re-check the cap before queueing follow-up work",
     "        return self._stop_reason is None",
     "        return True"),
    ("withhold rows on a non-billable run",
     "            await self._push(rows, None, kind)\n            return rows, 0",
     "            await self._push(rows, None, kind)\n            return [], 0"),
    ("accept a synthetic event name",
     "                if name.startswith(SYNTHETIC_PREFIX):",
     "                if False:"),
    ("retry the push even when the write landed",
     "                if written > 0:",
     "                if False:"),
    ("do not record rows written while the charge failed",
     "                    self._unbilled[event] += written  # type: ignore[index]",
     "                    self._unbilled[event] += 0"),
    ("count emitted rows in the status message",
     "_STOP_MESSAGES[self._stop_reason].format(rows=self.delivered)",
     '_STOP_MESSAGES[self._stop_reason].format(rows=self.delivered '
     '+ self._counters["duplicates"] + self._counters["unresolved"])'),
    ("count max_items before dedupe",
     "            if self._max_items is not None:\n                room = self._max_items - self.delivered",
     "            if self._max_items is not None:\n                room = self._max_items - self.delivered\n"
     "                candidates = list(rows)"),
    ("always charge the preferred name, priced or not",
     "            for name in candidates:\n                if name in priced:\n"
     "                    self._resolved[kind] = name\n                    break",
     "            for name in candidates:\n                if True:\n"
     "                    self._resolved[kind] = name\n                    break"),
    ("a refused run fee reads as paid",
     '        return await self._charge(event, 1, role="once") > 0',
     '        await self._charge(event, 1, role="once")\n        return True'),
    ("last stop reason wins",
     "        if self._stop_reason is None:\n            self._stop_reason = reason",
     "        self._stop_reason = reason"),
    ("no money reconciliation",
     "        if self._is_ppe and abs(unexplained) > 0.005:",
     "        if False:"),
    ("subtract the synthetic event twice",
     '            money["chargedUsd"] - money["expectedUsd"] - money["syntheticUsd"], 6',
     '            money["chargedUsd"] - money["expectedUsd"] - 2 * money["syntheticUsd"], 6'),
    ("never report the findings anywhere",
     '            if summary["findings"]:\n                await self._send_report(summary)',
     "            if False:\n                await self._send_report(summary)"),
    ("finish() is not idempotent, so a finally-block call loses the audit",
     "        if self._finished:\n",
     "        if False:\n"),
    ("a per-item slice bills one event per row, not per item",
     "            carrying = [(row, part.units(row)) for row in rows if part.carries(row)]\n"
     "            owed = sum(units for _, units in carrying)",
     "            carrying = [(row, 1) for row in rows if part.carries(row)]\n"
     "            owed = len(carrying)"),
    ("a partly billed per-item slice is blanked instead of cut back",
     "                if share < units:\n                    part.cut(row, share)",
     "                if share < units:\n                    part.blank(row)"),
    ("hand the billed units out to the last rows instead of the first",
     "            left = billed\n            for row, units in carrying:",
     "            left = billed\n            for row, units in reversed(carrying):"),
    ("no SDK cross-check in the audit",
     '            if sdk and sdk != data["charged"] + self._unbilled[event]:',
     "            if False:"),
]


def main() -> int:
    original = TARGET.read_text()
    backup = Path(tempfile.mkdtemp(prefix="mutation-sweep-")) / TARGET.name
    backup.write_text(original)
    caught: list[str] = []
    missed: list[str] = []
    try:
        for name, old, new in MUTANTS:
            occurrences = original.count(old)
            if occurrences != 1:
                print(f"STALE ANCHOR ({occurrences}x) - fix this mutant: {name}")
                missed.append(f"{name} (stale anchor)")
                continue
            TARGET.write_text(original.replace(old, new))
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "tests/", "-x", "-q"],
                    cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "?"
                failed = proc.returncode != 0
            except subprocess.TimeoutExpired:
                tail, failed = "the suite hung", True
            (caught if failed else missed).append(name)
            print(f"{'CAUGHT ' if failed else 'MISSED '}{name}  |  {tail}", flush=True)
    finally:
        TARGET.write_text(original)
        shutil.rmtree(backup.parent, ignore_errors=True)

    print(f"\n{len(caught)} caught / {len(caught) + len(missed)} mutants")
    for name in missed:
        print(" MISSED:", name)
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
