from __future__ import annotations
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("scraper.variants")


def _clean_text(val: Any) -> str:
    if val is None:
        return ""
    text = str(val).strip()
    # Strip HTML tags if any (e.g. <p>...</p>)
    text = re.sub(r"<[^>]+>", "", text).strip()
    return text


def _parse_price(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace(",", "").strip()
        match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def parse_woocommerce_variants(raw_variations: Any) -> List[Dict[str, Any]]:
    """
    Parse WooCommerce data-product_variations JSON payload or list of dicts.
    Returns normalized variant dictionaries containing:
    - variation_id: str
    - sku: str
    - size: str
    - color: str
    - price: Optional[float]
    - stock_status: str
    - attributes_json: str
    """
    if not raw_variations:
        return []

    variations_data: List[Dict[str, Any]] = []

    if isinstance(raw_variations, str):
        cleaned = raw_variations.strip()
        # Handle HTML entity encoding if Crawl4AI didn't decode it
        if "&quot;" in cleaned or "&#039;" in cleaned or "&amp;" in cleaned:
            cleaned = (
                cleaned.replace("&quot;", '"')
                .replace("&#039;", "'")
                .replace("&amp;", "&")
            )
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                variations_data = parsed
            elif isinstance(parsed, dict):
                variations_data = [parsed]
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(f"Failed to decode raw_variations JSON: {exc}")
            return []
    elif isinstance(raw_variations, list):
        variations_data = raw_variations
    elif isinstance(raw_variations, dict):
        variations_data = [raw_variations]

    normalized_variants: List[Dict[str, Any]] = []

    for item in variations_data:
        if not isinstance(item, dict):
            continue

        var_id = str(item.get("variation_id") or item.get("id") or "").strip()
        sku = _clean_text(item.get("sku") or "")

        # Attributes dict (e.g. {"attribute_size": "L", "attribute_color": "Blue"})
        attributes = item.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}

        size = ""
        color = ""
        other_attrs = {}

        for attr_key, attr_val in attributes.items():
            key_lower = str(attr_key).lower()
            val_clean = _clean_text(attr_val)
            if "size" in key_lower:
                size = val_clean
            elif "color" in key_lower or "colour" in key_lower:
                color = val_clean
            else:
                other_attrs[attr_key] = val_clean

        # Fallback to direct keys if not in attributes dict
        if not size and "size" in item:
            size = _clean_text(item.get("size"))
        if not color and "color" in item:
            color = _clean_text(item.get("color"))

        # Price parsing
        price_val = (
            item.get("display_price")
            or item.get("display_regular_price")
            or item.get("price")
        )
        parsed_price = _parse_price(price_val)

        # Stock status
        is_in_stock = item.get("is_in_stock")
        availability_html = item.get("availability_html") or ""
        stock_text = _clean_text(availability_html)

        if is_in_stock is True:
            stock_status = stock_text if stock_text else "In stock"
        elif is_in_stock is False:
            stock_status = stock_text if stock_text else "Out of stock"
        else:
            stock_status = stock_text or str(item.get("stock_status") or "Unknown")

        normalized_variants.append(
            {
                "variation_id": var_id,
                "sku": sku,
                "size": size,
                "color": color,
                "price": parsed_price,
                "stock_status": stock_status,
                "attributes_json": json.dumps(attributes),
            }
        )

    return normalized_variants
