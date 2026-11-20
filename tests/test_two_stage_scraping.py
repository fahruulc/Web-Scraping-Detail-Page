import os
from pathlib import Path
import pytest
from crawl4ai import AsyncWebCrawler
from src.config import TargetConfig, load_target_config
from src.extract_variants import parse_woocommerce_variants
from src.runner import run_pipeline, run_target
from src.storage import Storage
from src.validate import ProductRecord, validate_product, validate_variant

os.environ["CRAWL4AI_NO_CLOUD_NOTICE"] = "1"

CATALOG_FIXTURE = Path("tests/fixtures/ecommerce_catalog_sample.html")
DETAIL_FIXTURE = Path("tests/fixtures/ecommerce_detail_sample.html")


def test_detail_page_config_loader():
    target = load_target_config("targets/ecommerce.yaml")
    assert target.name == "ecommerce"
    assert target.detail_page is not None
    assert target.detail_page.enabled is True
    assert target.detail_page.link_field == "detail_url"
    assert target.detail_page.schema_file == "schemas/ecommerce_detail.json"
    assert target.detail_page.max_details == 5
    assert target.detail_page.extract_variants is True


def test_parse_woocommerce_variants():
    raw_json = (
        '[{"variation_id": 247, "sku": "MH09-L-Blue", '
        '"attributes": {"attribute_size": "L", "attribute_color": "Blue"}, '
        '"display_price": 69.0, "is_in_stock": true, '
        '"availability_html": "<p class=\\"stock in-stock\\">100 in stock</p>"}]'
    )
    variants = parse_woocommerce_variants(raw_json)
    assert len(variants) == 1
    v = variants[0]
    assert v["variation_id"] == "247"
    assert v["sku"] == "MH09-L-Blue"
    assert v["size"] == "L"
    assert v["color"] == "Blue"
    assert v["price"] == 69.0
    assert "100 in stock" in v["stock_status"]


def test_storage_one_to_many_variants(tmp_path):
    db_file = tmp_path / "test_variants.db"
    storage = Storage(db_path=db_file)

    parent_rec = ProductRecord(
        target="ecommerce",
        source_url="https://example.com/product/hoodie",
        name="Abominable Hoodie",
        price=69.0,
        stock="In stock",
        sku="MH09",
        description="Winter warm hoodie",
    )
    res = storage.save_product(parent_rec)
    product_id = res["id"]
    assert product_id > 0

    variants_data = [
        {
            "variation_id": "247",
            "sku": "MH09-L-Blue",
            "size": "L",
            "color": "Blue",
            "price": 69.0,
            "stock_status": "In stock",
            "attributes_json": '{"size": "L", "color": "Blue"}',
        },
        {
            "variation_id": "248",
            "sku": "MH09-M-Green",
            "size": "M",
            "color": "Green",
            "price": 69.0,
            "stock_status": "In stock",
            "attributes_json": '{"size": "M", "color": "Green"}',
        },
    ]

    # 1. Insert variants
    var_res1 = storage.save_variants(product_id=product_id, variants=variants_data)
    assert var_res1["inserted"] == 2
    assert storage.count_variants(product_id) == 2

    saved = storage.get_variants(product_id)
    assert len(saved) == 2
    assert saved[0]["product_id"] == product_id
    assert saved[0]["size"] == "L"
    assert saved[1]["size"] == "M"

    # 2. Idempotency test: Re-saving identical variants causes unchanged, no duplicates
    var_res2 = storage.save_variants(product_id=product_id, variants=variants_data)
    assert var_res2["inserted"] == 0
    assert var_res2["unchanged"] == 2
    assert storage.count_variants(product_id) == 2

    # 3. Update test: Changed price or stock updates variant
    updated_variants = [
        {
            "variation_id": "247",
            "sku": "MH09-L-Blue",
            "size": "L",
            "color": "Blue",
            "price": 75.0,  # Price updated
            "stock_status": "Low stock",
            "attributes_json": '{"size": "L", "color": "Blue"}',
        }
    ]
    var_res3 = storage.save_variants(product_id=product_id, variants=updated_variants)
    assert var_res3["updated"] == 1
    saved_after = storage.get_variants(product_id)
    v247 = next(v for v in saved_after if v["variation_id"] == "247")
    assert v247["price"] == 75.0
    assert v247["stock_status"] == "Low stock"


@pytest.mark.asyncio
async def test_two_stage_pipeline_offline(tmp_path):
    db_file = tmp_path / "offline_pipeline.db"
    storage = Storage(db_path=db_file)

    target = load_target_config("targets/ecommerce.yaml")
    # For this offline unit test, limit to 1 detail item
    target.detail_page.max_details = 1

    catalog_html = CATALOG_FIXTURE.read_text(encoding="utf-8")
    detail_html = DETAIL_FIXTURE.read_text(encoding="utf-8")

    async with AsyncWebCrawler() as crawler:
        stats = await run_target(
            target=target,
            storage=storage,
            crawler=crawler,
            fixture_content=catalog_html,
            detail_fixture_content=detail_html,
        )

    assert stats["urls_processed"] == 1
    assert stats["items_valid"] == 1
    assert stats["variants_inserted"] == 3

    # Check products in DB
    prods = storage.get_all_products(target="ecommerce")
    assert len(prods) == 1
    assert prods[0]["name"] == "Abominable Hoodie"

    # Check variants in DB (Relational One-to-Many)
    variants = storage.get_variants(product_id=prods[0]["id"])
    assert len(variants) == 3
    sizes = {v["size"] for v in variants}
    assert sizes == {"L", "M", "S"}
