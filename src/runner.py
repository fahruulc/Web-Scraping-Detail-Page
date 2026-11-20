from __future__ import annotations
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin
from crawl4ai import AsyncWebCrawler
from src.config import TargetConfig, load_all_targets, load_target_config
from src.extract_css import extract_with_css
from src.extract_variants import parse_woocommerce_variants
from src.storage import Storage
from src.validate import validate_generic, validate_product

# Silence optional cloud notice if desired
os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scraper.runner")


async def run_target(
    target: TargetConfig,
    storage: Storage,
    crawler: AsyncWebCrawler,
    fixture_content: Optional[str] = None,
    detail_fixture_content: Optional[str] = None,
) -> Dict[str, int]:
    """
    Run scraping for a single target.
    Supports both Single-Page and Two-Stage (Master-Detail) scraping pipelines.
    Returns summary statistics for items processed.
    """
    logger.info(f"--- Starting target: {target.name} (mode: {target.mode}) ---")
    stats = {
        "urls_processed": 0,
        "items_extracted": 0,
        "items_valid": 0,
        "items_invalid": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "variants_inserted": 0,
        "variants_updated": 0,
        "variants_unchanged": 0,
    }

    if target.mode != "css":
        raise NotImplementedError(
            f"Mode '{target.mode}' is not yet implemented (scheduled for M3). Use mode 'css'."
        )

    for i, url in enumerate(target.start_urls):
        if i >= target.max_pages:
            logger.info(f"Target {target.name}: Reached max_pages ({target.max_pages}). Stopping.")
            break

        logger.info(f"Processing URL [{i+1}/{len(target.start_urls)}]: {url}")
        try:
            items = await extract_with_css(
                crawler=crawler,
                url=url,
                schema=target.schema_file,
                raw_html=fixture_content,
                scroll_full_page=target.scroll_full_page,
            )
            stats["urls_processed"] += 1
            stats["items_extracted"] += len(items)
            logger.info(f"Extracted {len(items)} catalog items from {url}")

            # Check if Two-Stage Scraping is configured for this target
            is_two_stage = target.detail_page and target.detail_page.enabled

            items_to_process = items
            if is_two_stage and target.detail_page.max_details:
                items_to_process = items[: target.detail_page.max_details]
                logger.info(
                    f"Two-stage scraping enabled: limiting to {len(items_to_process)} detail items (max_details={target.detail_page.max_details})."
                )

            for raw_item in items_to_process:
                merged_item = dict(raw_item)
                item_source_url = url
                variants: List[Dict] = []

                if is_two_stage:
                    link_field = target.detail_page.link_field
                    detail_url = raw_item.get(link_field)
                    if detail_url:
                        detail_url = urljoin(url, str(detail_url))
                        item_source_url = detail_url
                        logger.info(f"Fetching detail page: {detail_url}")

                        try:
                            detail_results = await extract_with_css(
                                crawler=crawler,
                                url=detail_url,
                                schema=target.detail_page.schema_file,
                                raw_html=detail_fixture_content,
                                scroll_full_page=target.scroll_full_page,
                            )
                            if detail_results:
                                detail_data = detail_results[0]
                                # Merge detail data into catalog item (detail fields take precedence if present)
                                for k, v in detail_data.items():
                                    if v is not None and v != "":
                                        merged_item[k] = v

                                # Extract variants if enabled
                                if target.detail_page.extract_variants:
                                    raw_vars = detail_data.get("raw_variations")
                                    if raw_vars:
                                        variants = parse_woocommerce_variants(raw_vars)
                                        logger.info(
                                            f"Found {len(variants)} variants for '{merged_item.get('name') or merged_item.get('title')}'"
                                        )

                        except Exception as det_err:
                            logger.error(f"Error fetching detail {detail_url}: {det_err}")

                        # Polite delay between detail page requests when hitting live server
                        if fixture_content is None and detail_fixture_content is None:
                            await asyncio.sleep(target.rate_limit_seconds)

                # Map title -> name if detail schema used 'title'
                if "name" not in merged_item and "title" in merged_item:
                    merged_item["name"] = merged_item["title"]

                # Check if item conforms to standard ProductRecord (has 'name' and 'price')
                if "name" in merged_item and "price" in merged_item:
                    record, err = validate_product(merged_item, target=target.name, source_url=item_source_url)
                    if record is None:
                        stats["items_invalid"] += 1
                        logger.warning(f"Invalid record rejected: {merged_item} | Reason: {err}")
                        continue

                    stats["items_valid"] += 1
                    res = storage.save_product(record)
                    action = res.get("action")
                    product_id = res.get("id")

                    if action == "inserted":
                        stats["inserted"] += 1
                    elif action == "updated":
                        stats["updated"] += 1
                        logger.info(f"Price/stock updated for '{record.name}': {res}")
                    elif action == "unchanged":
                        stats["unchanged"] += 1

                    # Save variants if any (Relational 1-to-Many)
                    if variants and product_id:
                        var_stats = storage.save_variants(product_id=product_id, variants=variants)
                        stats["variants_inserted"] += var_stats["inserted"]
                        stats["variants_updated"] += var_stats["updated"]
                        stats["variants_unchanged"] += var_stats["unchanged"]

                else:
                    # Dynamic / generic schema for custom target (e.g. jobs, articles, etc.)
                    clean_item, err = validate_generic(merged_item, target=target.name, source_url=item_source_url)
                    if clean_item is None:
                        stats["items_invalid"] += 1
                        logger.warning(f"Invalid generic record rejected: {merged_item} | Reason: {err}")
                        continue

                    stats["items_valid"] += 1
                    res = storage.save_generic(
                        target=target.name,
                        source_url=item_source_url,
                        data=clean_item,
                        table_name=target.name,
                    )
                    action = res.get("action")
                    if action == "inserted":
                        stats["inserted"] += 1
                    elif action == "updated":
                        stats["updated"] += 1
                        logger.info(f"Dynamic record updated in '{target.name}': {res}")
                    elif action == "unchanged":
                        stats["unchanged"] += 1

        except Exception as exc:
            logger.error(f"Error scraping URL {url} on target {target.name}: {exc}", exc_info=True)

        # Rate limiting between top-level URLs if not offline fixture
        if fixture_content is None and i < len(target.start_urls) - 1:
            await asyncio.sleep(target.rate_limit_seconds)

    logger.info(
        f"Completed target '{target.name}': "
        f"{stats['items_valid']} valid ({stats['inserted']} inserted, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged), {stats['items_invalid']} invalid, "
        f"Variants: {stats['variants_inserted']} inserted, {stats['variants_updated']} updated."
    )
    return stats


async def run_pipeline(
    target_name: Optional[str] = None,
    targets_dir: str | Path = "targets",
    db_path: str | Path = "data/scraper.db",
    fixture_path: Optional[str | Path] = None,
    detail_fixture_path: Optional[str | Path] = None,
) -> Dict[str, Dict[str, int]]:
    storage = Storage(db_path=db_path)

    fixture_content = None
    if fixture_path:
        fixture_file = Path(fixture_path)
        if fixture_file.exists():
            fixture_content = fixture_file.read_text(encoding="utf-8")
        else:
            raise FileNotFoundError(f"Fixture file not found: {fixture_path}")

    detail_fixture_content = None
    if detail_fixture_path:
        detail_fixture_file = Path(detail_fixture_path)
        if detail_fixture_file.exists():
            detail_fixture_content = detail_fixture_file.read_text(encoding="utf-8")
        else:
            raise FileNotFoundError(f"Detail fixture file not found: {detail_fixture_path}")

    # Determine targets to run
    if target_name:
        target_file = Path(targets_dir) / f"{target_name}.yaml"
        if not target_file.exists():
            raise FileNotFoundError(f"Target config file not found: {target_file}")
        targets = [load_target_config(target_file)]
    else:
        targets = load_all_targets(targets_dir)

    if not targets:
        logger.warning(f"No target configurations found in {targets_dir}")
        return {}

    overall_results: Dict[str, Dict[str, int]] = {}

    async with AsyncWebCrawler() as crawler:
        for tgt in targets:
            try:
                stats = await run_target(
                    target=tgt,
                    storage=storage,
                    crawler=crawler,
                    fixture_content=fixture_content,
                    detail_fixture_content=detail_fixture_content,
                )
                overall_results[tgt.name] = stats
            except Exception as tgt_err:
                # One target failing must not stop other targets
                logger.error(f"Target '{tgt.name}' failed with error: {tgt_err}", exc_info=True)
                overall_results[tgt.name] = {"error": 1}

    return overall_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Config-Driven Web Scraper with Crawl4AI")
    parser.add_argument("--target", "-t", type=str, help="Specific target name (without .yaml)")
    parser.add_argument("--targets-dir", type=str, default="targets", help="Targets directory")
    parser.add_argument("--db", type=str, default="data/scraper.db", help="Path to SQLite database")
    parser.add_argument("--fixture", "-f", type=str, help="Path to offline HTML fixture for testing")
    parser.add_argument("--detail-fixture", type=str, help="Path to offline detail HTML fixture for testing")
    args = parser.parse_args()

    results = asyncio.run(
        run_pipeline(
            target_name=args.target,
            targets_dir=args.targets_dir,
            db_path=args.db,
            fixture_path=args.fixture,
            detail_fixture_path=args.detail_fixture,
        )
    )
    print("\n=== Scraping Execution Summary ===")
    for tgt_name, res in results.items():
        print(f"Target [{tgt_name}]: {res}")


if __name__ == "__main__":
    main()

