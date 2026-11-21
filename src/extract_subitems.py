from __future__ import annotations
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("scraper.subitems")


def clean_text_value(val: Any) -> str:
    """Strip HTML tags and excessive whitespace from a value."""
    if val is None:
        return ""
    text = str(val).strip()
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", text).strip()
    return text


def parse_numeric_price(val: Any) -> Optional[float]:
    """Parse numeric price safely from string or number."""
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


def _normalize_key(key: str) -> str:
    """
    Normalize key names by removing common prefix patterns like attribute_ or attribute_pa_
    and converting to lowercase snake_case identifier.
    """
    clean_k = str(key).strip()
    # Remove WooCommerce prefixes if present
    clean_k = re.sub(r"^attribute_pa_", "", clean_k, flags=re.IGNORECASE)
    clean_k = re.sub(r"^attribute_", "", clean_k, flags=re.IGNORECASE)
    clean_k = re.sub(r"[^a-zA-Z0-9_]", "_", clean_k).strip("_").lower()
    return clean_k or "item"


def parse_generic_sub_items(raw_data: Any) -> List[Dict[str, Any]]:
    """
    Universal sub-items parser.
    Flattens any list of dictionaries, JSON strings, or nested objects into
    clean flat dictionaries without hardcoding specific domains (e.g. apparel, menus, tech).
    """
    if not raw_data:
        return []

    items_list: List[Dict[str, Any]] = []

    # 1. Parse JSON string if needed
    if isinstance(raw_data, str):
        cleaned = raw_data.strip()
        if not cleaned:
            return []
        # Unescape HTML entities
        if "&quot;" in cleaned or "&#039;" in cleaned or "&amp;" in cleaned:
            cleaned = (
                cleaned.replace("&quot;", '"')
                .replace("&#039;", "'")
                .replace("&amp;", "&")
            )
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                items_list = parsed
            elif isinstance(parsed, dict):
                items_list = [parsed]
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(f"Failed to decode sub-items JSON string: {exc}")
            return []
    elif isinstance(raw_data, list):
        items_list = raw_data
    elif isinstance(raw_data, dict):
        items_list = [raw_data]

    normalized_sub_items: List[Dict[str, Any]] = []

    for item in items_list:
        if not isinstance(item, dict):
            continue

        flat_record: Dict[str, Any] = {}

        for k, v in item.items():
            norm_k = _normalize_key(k)

            # Flatten nested dictionaries (e.g. {"attributes": {"size": "L", "color": "Blue"}})
            if isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    norm_sub_k = _normalize_key(sub_k)
                    flat_record[norm_sub_k] = clean_text_value(sub_v)
            elif isinstance(v, (list, tuple)):
                flat_record[norm_k] = json.dumps(v)
            elif "price" in norm_k:
                price_val = parse_numeric_price(v)
                flat_record[norm_k] = price_val if price_val is not None else clean_text_value(v)
            elif isinstance(v, bool):
                flat_record[norm_k] = "true" if v else "false"
            else:
                flat_record[norm_k] = clean_text_value(v)

        # Standardize stock text if availability_html or is_in_stock exists
        if "availability_html" in flat_record and flat_record["availability_html"]:
            flat_record["stock_status"] = clean_text_value(flat_record["availability_html"])
        elif "is_in_stock" in flat_record and "stock_status" not in flat_record:
            flat_record["stock_status"] = "In stock" if flat_record["is_in_stock"] == "true" else "Out of stock"

        # Map display_price to price if price not explicitly present
        if "price" not in flat_record and "display_price" in flat_record:
            flat_record["price"] = flat_record["display_price"]

        if flat_record:
            normalized_sub_items.append(flat_record)

    return normalized_sub_items
