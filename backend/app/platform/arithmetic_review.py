"""Verify explicit, affirmative decimal reconciliation lines in an analysis result."""

import re
from decimal import Decimal

# This deliberately accepts only a complete assertion, not an equation embedded in
# quotations, an example, a multi-operation expression, or a claim of approximation.
NUMBER = r"([+-]?[0-9]{1,15}(?:\.[0-9]{1,8})?)"
ASSERTION = re.compile(
    r"\s*(?:[-*]\s+)?(?:(?:复算|核算|合计|加总|金额核对|数量核对)\s*[：:]\s*)?"
    + NUMBER
    + r"\s*([+＋−-])\s*"
    + NUMBER
    + r"\s*[=＝]\s*"
    + NUMBER
    + r"\s*(?:[，,]?\s*(?:且一致|核对一致|与总数一致|复算一致))?\s*[。.]?\s*"
)


def reconciliation_errors(text):
    checked, errors, fence = 0, [], None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            delimiter = stripped[:3]
            if fence in (None, delimiter):
                fence = delimiter if fence is None else None
            continue
        if fence:
            continue
        match = ASSERTION.fullmatch(line)
        if not match:
            continue
        if not re.search(r"复算|核算|合计|加总|核对|一致", line):
            continue
        left, operator, right, claimed = match.groups()
        calculated = (
            Decimal(left) + Decimal(right) if operator in {"+", "＋"} else Decimal(left) - Decimal(right)
        )
        checked += 1
        if Decimal(claimed) != calculated and len(errors) < 4:
            errors.append(f"{left} {operator} {right} 应为 {calculated}，交付写为 {claimed}")
    return checked, errors
