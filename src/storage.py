from __future__ import annotations
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.validate import ProductRecord


class Storage:
    def __init__(self, db_path: str | Path = "data/scraper.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price REAL NOT NULL,
                    stock TEXT NOT NULL,
                    sku TEXT,
                    description TEXT,
                    scraped_at TEXT NOT NULL,
                    UNIQUE(target, source_url, name)
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    price REAL NOT NULL,
                    stock TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS product_variants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    variation_id TEXT,
                    sku TEXT,
                    size TEXT,
                    color TEXT,
                    price REAL,
                    stock_status TEXT,
                    attributes_json TEXT,
                    scraped_at TEXT NOT NULL,
                    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
                    UNIQUE(product_id, variation_id, size, color)
                );

                CREATE INDEX IF NOT EXISTS idx_products_target ON products(target);
                CREATE INDEX IF NOT EXISTS idx_history_product_id ON price_history(product_id);
                CREATE INDEX IF NOT EXISTS idx_variant_product_id ON product_variants(product_id);
                """
            )
            # Ensure optional columns exist if products table was created in earlier versions
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(products)")
            existing_cols = {row["name"] for row in cur.fetchall()}
            if "sku" not in existing_cols:
                cur.execute("ALTER TABLE products ADD COLUMN sku TEXT;")
            if "description" not in existing_cols:
                cur.execute("ALTER TABLE products ADD COLUMN description TEXT;")
            conn.commit()

    def save_product(self, record: ProductRecord) -> Dict[str, Any]:
        scraped_at_iso = record.scraped_at.isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, price, stock, sku, description FROM products
                WHERE target = ? AND source_url = ? AND name = ?
                """,
                (record.target, record.source_url, record.name),
            )
            row = cur.fetchone()

            if row is None:
                # Insert new product
                cur.execute(
                    """
                    INSERT INTO products (target, source_url, name, price, stock, sku, description, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.target,
                        record.source_url,
                        record.name,
                        record.price,
                        record.stock,
                        record.sku,
                        record.description,
                        scraped_at_iso,
                    ),
                )
                product_id = cur.lastrowid
                # Record initial price history
                cur.execute(
                    """
                    INSERT INTO price_history (product_id, price, stock, recorded_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (product_id, record.price, record.stock, scraped_at_iso),
                )
                conn.commit()
                return {"action": "inserted", "id": product_id}

            product_id = row["id"]
            old_price = row["price"]
            old_stock = row["stock"]

            price_changed = abs(old_price - record.price) > 0.0001
            stock_changed = old_stock != record.stock

            if price_changed or stock_changed:
                cur.execute(
                    """
                    UPDATE products
                    SET price = ?, stock = ?, sku = COALESCE(?, sku), description = COALESCE(?, description), scraped_at = ?
                    WHERE id = ?
                    """,
                    (record.price, record.stock, record.sku, record.description, scraped_at_iso, product_id),
                )
                cur.execute(
                    """
                    INSERT INTO price_history (product_id, price, stock, recorded_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (product_id, record.price, record.stock, scraped_at_iso),
                )
                conn.commit()
                return {
                    "action": "updated",
                    "id": product_id,
                    "old_price": old_price,
                    "new_price": record.price,
                    "old_stock": old_stock,
                    "new_stock": record.stock,
                }
            else:
                # Update scraped_at and metadata only, no duplicate history
                cur.execute(
                    """
                    UPDATE products
                    SET sku = COALESCE(?, sku), description = COALESCE(?, description), scraped_at = ?
                    WHERE id = ?
                    """,
                    (record.sku, record.description, scraped_at_iso, product_id),
                )
                conn.commit()
                return {"action": "unchanged", "id": product_id}

    def get_product(self, target: str, source_url: str, name: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, target, source_url, name, price, stock, scraped_at
                FROM products
                WHERE target = ? AND source_url = ? AND name = ?
                """,
                (target, source_url, name),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_products(self, target: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            if target:
                cur.execute(
                    "SELECT id, target, source_url, name, price, stock, scraped_at FROM products WHERE target = ? ORDER BY id",
                    (target,),
                )
            else:
                cur.execute(
                    "SELECT id, target, source_url, name, price, stock, scraped_at FROM products ORDER BY id"
                )
            return [dict(row) for row in cur.fetchall()]

    def get_price_history(self, product_id: int) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, product_id, price, stock, recorded_at FROM price_history WHERE product_id = ? ORDER BY id",
                (product_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def count_products(self, target: Optional[str] = None) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            if target:
                cur.execute("SELECT COUNT(*) FROM products WHERE target = ?", (target,))
            else:
                cur.execute("SELECT COUNT(*) FROM products")
            return int(cur.fetchone()[0])

    def save_variants(self, product_id: int, variants: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Save or update product variants linked to product_id (1-to-Many relation).
        Enforces idempotency using (product_id, variation_id, size, color).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        stats = {"inserted": 0, "updated": 0, "unchanged": 0}
        if not variants:
            return stats

        with self._get_connection() as conn:
            cur = conn.cursor()
            for v in variants:
                variation_id = str(v.get("variation_id") or "")
                sku = str(v.get("sku") or "")
                size = str(v.get("size") or "")
                color = str(v.get("color") or "")
                price = v.get("price")
                stock_status = str(v.get("stock_status") or "")
                attributes_json = str(v.get("attributes_json") or "")

                cur.execute(
                    """
                    SELECT id, price, stock_status, sku FROM product_variants
                    WHERE product_id = ? AND variation_id = ? AND size = ? AND color = ?
                    """,
                    (product_id, variation_id, size, color),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        """
                        INSERT INTO product_variants 
                        (product_id, variation_id, sku, size, color, price, stock_status, attributes_json, scraped_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            product_id,
                            variation_id,
                            sku,
                            size,
                            color,
                            price,
                            stock_status,
                            attributes_json,
                            now_iso,
                        ),
                    )
                    stats["inserted"] += 1
                else:
                    var_id = row["id"]
                    old_price = row["price"]
                    old_stock = row["stock_status"]
                    old_sku = row["sku"]

                    price_changed = False
                    if old_price is not None and price is not None:
                        price_changed = abs(old_price - price) > 0.0001
                    elif old_price != price:
                        price_changed = True

                    stock_changed = old_stock != stock_status
                    sku_changed = bool(sku and (old_sku != sku))

                    if price_changed or stock_changed or sku_changed:
                        cur.execute(
                            """
                            UPDATE product_variants
                            SET price = ?, stock_status = ?, sku = COALESCE(NULLIF(?, ''), sku), scraped_at = ?
                            WHERE id = ?
                            """,
                            (price, stock_status, sku, now_iso, var_id),
                        )
                        stats["updated"] += 1
                    else:
                        cur.execute(
                            "UPDATE product_variants SET scraped_at = ? WHERE id = ?",
                            (now_iso, var_id),
                        )
                        stats["unchanged"] += 1
            conn.commit()
        return stats

    def get_variants(self, product_id: int) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, product_id, variation_id, sku, size, color, price, stock_status, attributes_json, scraped_at
                FROM product_variants
                WHERE product_id = ?
                ORDER BY id
                """,
                (product_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def count_variants(self, product_id: Optional[int] = None) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            if product_id is not None:
                cur.execute("SELECT COUNT(*) FROM product_variants WHERE product_id = ?", (product_id,))
            else:
                cur.execute("SELECT COUNT(*) FROM product_variants")
            return int(cur.fetchone()[0])

    def _sanitize_identifier(self, name: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_]", "_", str(name)).strip("_").lower()
        if not clean:
            clean = "item"
        if clean[0].isdigit():
            clean = f"t_{clean}"
        return clean

    def _ensure_table(self, table_name: str, data: Dict[str, Any]) -> str:
        table = self._sanitize_identifier(table_name)
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS "{table}" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    scraped_at TEXT NOT NULL
                );
                """
            )
            cur.execute(f'PRAGMA table_info("{table}")')
            existing_cols = {row["name"] for row in cur.fetchall()}
            for key in data.keys():
                clean_col = self._sanitize_identifier(key)
                if clean_col not in existing_cols:
                    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{clean_col}" TEXT;')
                    existing_cols.add(clean_col)
            conn.commit()
        return table

    def save_generic(
        self,
        target: str,
        source_url: str,
        data: Dict[str, Any],
        table_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Save a generic/dynamic dictionary to a target-specific table with dynamic columns.
        Supports automatic column creation, deduplication, and updating.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        table = self._ensure_table(table_name or target, data)

        clean_data = {
            self._sanitize_identifier(k): (str(v) if v is not None else "")
            for k, v in data.items()
        }

        # Choose primary identity column for deduplication
        candidate_keys = ["job_name", "title", "name", "id", "url", "link"]
        identity_col = None
        for cand in candidate_keys:
            if cand in clean_data and clean_data[cand]:
                identity_col = cand
                break
        if not identity_col and clean_data:
            identity_col = next(iter(clean_data.keys()))

        with self._get_connection() as conn:
            cur = conn.cursor()
            row = None
            if identity_col:
                cur.execute(
                    f'SELECT * FROM "{table}" WHERE target = ? AND "{identity_col}" = ?',
                    (target, clean_data[identity_col]),
                )
                row = cur.fetchone()

            if row is None:
                cols = ["target", "source_url", "scraped_at"] + list(clean_data.keys())
                placeholders = ["?"] * len(cols)
                values = [target, source_url, now_iso] + list(clean_data.values())
                col_names = ", ".join(f'"{c}"' for c in cols)
                sql = f'INSERT INTO "{table}" ({col_names}) VALUES ({", ".join(placeholders)})'
                cur.execute(sql, values)
                conn.commit()
                return {"action": "inserted", "id": cur.lastrowid}

            existing_dict = dict(row)
            changed = any(existing_dict.get(k) != clean_data[k] for k in clean_data)
            record_id = row["id"]

            if changed:
                set_clauses = [f'"{k}" = ?' for k in clean_data.keys()]
                set_clauses.append('"scraped_at" = ?')
                values = list(clean_data.values()) + [now_iso, record_id]
                sql = f'UPDATE "{table}" SET {", ".join(set_clauses)} WHERE id = ?'
                cur.execute(sql, values)
                conn.commit()
                return {"action": "updated", "id": record_id}
            else:
                cur.execute(
                    f'UPDATE "{table}" SET scraped_at = ? WHERE id = ?',
                    (now_iso, record_id),
                )
                conn.commit()
                return {"action": "unchanged", "id": record_id}

    def get_tables(self) -> List[str]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            return [row["name"] for row in cur.fetchall()]

    def get_records(self, table_name: str, target: Optional[str] = None) -> List[Dict[str, Any]]:
        table = self._sanitize_identifier(table_name)
        with self._get_connection() as conn:
            cur = conn.cursor()
            if target:
                cur.execute(f'SELECT * FROM "{table}" WHERE target = ? ORDER BY id', (target,))
            else:
                cur.execute(f'SELECT * FROM "{table}" ORDER BY id')
            return [dict(row) for row in cur.fetchall()]

    def count_records(self, table_name: str, target: Optional[str] = None) -> int:
        table = self._sanitize_identifier(table_name)
        with self._get_connection() as conn:
            cur = conn.cursor()
            if target:
                cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE target = ?', (target,))
            else:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            row = cur.fetchone()
            return int(row[0]) if row else 0

