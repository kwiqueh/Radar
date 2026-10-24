from __future__ import annotations

import re

from skin_radar.models import ItemIdentity

EXTERIORS = {
    "Factory New": "FN",
    "Minimal Wear": "MW",
    "Field-Tested": "FT",
    "Well-Worn": "WW",
    "Battle-Scarred": "BS",
}


def identity_from_market_hash_name(name: str, **attributes: object) -> ItemIdentity:
    exterior = next((code for full, code in EXTERIORS.items() if f"({full})" in name), None)
    return ItemIdentity(
        market_hash_name=name,
        exterior=exterior,
        stattrak=name.startswith("StatTrak™"),
        souvenir=name.startswith("Souvenir"),
        **attributes,
    )


def can_compare(left: ItemIdentity, right: ItemIdentity) -> tuple[bool, tuple[str, ...]]:
    """Return whether quotes refer to the same base SKU and any data-quality warnings.

    `market_hash_name` encodes exterior, StatTrak and Souvenir and is therefore always exact.
    Optional premium attributes are required to agree only when both sources provide them.
    Missing destination attributes produce an explicit warning rather than a premium claim.
    """
    if left.market_hash_name != right.market_hash_name:
        return False, ()
    if left.exterior != right.exterior or left.stattrak != right.stattrak:
        return False, ()
    if left.souvenir != right.souvenir:
        return False, ()

    warnings: list[str] = []
    for label, a, b in (
        ("float", left.float_value, right.float_value),
        ("pattern", left.paint_seed, right.paint_seed),
        ("stickers", left.stickers or None, right.stickers or None),
    ):
        if a is not None and b is not None and a != b:
            return False, ()
        if (a is None) != (b is None):
            warnings.append(f"{label}: одна из площадок не предоставляет атрибут")
    return True, tuple(warnings)


def safe_item_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
