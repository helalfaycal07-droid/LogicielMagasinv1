"""Base SQLite locale : connexion, transactions, migrations de schéma."""

import itertools
import sqlite3
from contextlib import contextmanager

from .clock import now_iso

_savepoint_ids = itertools.count(1)

MIGRATION_1 = """
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE document_sequences (code TEXT PRIMARY KEY, last_number INTEGER NOT NULL DEFAULT 0);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL,
    entity TEXT, entity_id INTEGER, details TEXT
);

CREATE TABLE warehouses (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    category TEXT,
    brand TEXT,
    cost_centimes INTEGER NOT NULL DEFAULT 0 CHECK (cost_centimes >= 0),
    price_centimes INTEGER NOT NULL CHECK (price_centimes >= 0),
    price_includes_tax INTEGER NOT NULL DEFAULT 1 CHECK (price_includes_tax IN (0, 1)),
    tax_rate_bp INTEGER NOT NULL DEFAULT 1900 CHECK (tax_rate_bp >= 0),
    min_stock INTEGER NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
    track_serial INTEGER NOT NULL DEFAULT 0 CHECK (track_serial IN (0, 1)),
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE stock_movements (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    movement_type TEXT NOT NULL CHECK (movement_type IN
        ('reception_achat','vente','retour_client','retour_fournisseur','ajustement','inventaire','transfert')),
    quantity INTEGER NOT NULL CHECK (quantity <> 0),
    qty_before INTEGER NOT NULL,
    qty_after INTEGER NOT NULL,
    lot TEXT,
    ref_type TEXT,
    ref_id INTEGER,
    ref_number TEXT,
    counterparty TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_movements_product ON stock_movements(product_id, warehouse_id);

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    dossier_number TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT,
    phone2 TEXT,
    address TEXT,
    status TEXT NOT NULL DEFAULT 'actif' CHECK (status IN
        ('actif','en_retard','contentieux','bloque','irrecouvrable','archive')),
    credit_allowed INTEGER NOT NULL DEFAULT 0 CHECK (credit_allowed IN (0, 1)),
    credit_limit_centimes INTEGER CHECK (credit_limit_centimes IS NULL OR credit_limit_centimes >= 0),
    created_at TEXT NOT NULL
);
CREATE TABLE customer_adjustments (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_centimes INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE cash_registers (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
CREATE TABLE cash_sessions (
    id INTEGER PRIMARY KEY,
    register_id INTEGER NOT NULL REFERENCES cash_registers(id),
    opened_at TEXT NOT NULL,
    opening_centimes INTEGER NOT NULL CHECK (opening_centimes >= 0),
    closed_at TEXT,
    counted_centimes INTEGER,
    expected_centimes INTEGER,
    difference_centimes INTEGER,
    difference_reason TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed'))
);
CREATE UNIQUE INDEX idx_one_open_session ON cash_sessions(register_id) WHERE status = 'open';
CREATE TABLE cash_movements (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES cash_sessions(id),
    kind TEXT NOT NULL CHECK (kind IN
        ('ouverture','vente_especes','acompte','paiement_client','depense','entree','sortie','fermeture')),
    amount_centimes INTEGER NOT NULL,
    balance_before INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    ref_type TEXT,
    ref_id INTEGER,
    ref_number TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    customer_id INTEGER REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'validee' CHECK (status IN ('brouillon','validee','retournee','archivee')),
    total_centimes INTEGER NOT NULL CHECK (total_centimes >= 0),
    tendered_centimes INTEGER NOT NULL DEFAULT 0,
    change_centimes INTEGER NOT NULL DEFAULT 0,
    session_id INTEGER REFERENCES cash_sessions(id),
    created_at TEXT NOT NULL
);
CREATE TABLE sale_items (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_centimes INTEGER NOT NULL CHECK (unit_price_centimes >= 0),
    total_centimes INTEGER NOT NULL CHECK (total_centimes >= 0)
);
CREATE TABLE sale_payments (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    method TEXT NOT NULL CHECK (method IN ('especes','carte','ccp','virement','cheque','avoir')),
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes > 0)
);

CREATE TABLE customer_payments (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    method TEXT NOT NULL CHECK (method IN ('especes','carte','ccp','virement','cheque','avoir')),
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes > 0),
    session_id INTEGER REFERENCES cash_sessions(id),
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE payment_plans (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL UNIQUE REFERENCES sales(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    principal_centimes INTEGER NOT NULL CHECK (principal_centimes > 0),
    installment_count INTEGER NOT NULL CHECK (installment_count > 0),
    first_due_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE installments (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES payment_plans(id),
    number INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes > 0),
    paid_centimes INTEGER NOT NULL DEFAULT 0 CHECK (paid_centimes >= 0 AND paid_centimes <= amount_centimes),
    UNIQUE (plan_id, number)
);
CREATE TABLE payment_allocations (
    id INTEGER PRIMARY KEY,
    payment_id INTEGER NOT NULL REFERENCES customer_payments(id),
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    installment_id INTEGER REFERENCES installments(id),
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes > 0)
);
CREATE TABLE cheques (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    installment_id INTEGER REFERENCES installments(id),
    number TEXT NOT NULL,
    bank TEXT,
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes > 0),
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'en_dossier' CHECK (status IN
        ('en_dossier','remis_banque','encaisse','impaye','a_restituer','restitue')),
    note TEXT,
    created_at TEXT NOT NULL
);
"""

MIGRATION_2 = """
CREATE TABLE product_serials (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    imei1 TEXT NOT NULL UNIQUE,
    imei2 TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'en_stock' CHECK (status IN ('en_stock','vendu','retourne','sorti')),
    entry_movement_id INTEGER REFERENCES stock_movements(id),
    sale_id INTEGER REFERENCES sales(id),
    sold_at TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    CHECK (imei2 IS NULL OR imei2 <> imei1)
);
CREATE INDEX idx_serials_product ON product_serials(product_id, status);
"""

MIGRATION_3 = """
CREATE TABLE purchase_receipts (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    supplier TEXT NOT NULL,
    supplier_reference TEXT,
    total_centimes INTEGER NOT NULL CHECK (total_centimes >= 0),
    created_at TEXT NOT NULL
);
CREATE TABLE purchase_receipt_items (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES purchase_receipts(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_cost_centimes INTEGER NOT NULL CHECK (unit_cost_centimes >= 0),
    UNIQUE (receipt_id, product_id)
);
CREATE TABLE supplier_payments (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES purchase_receipts(id),
    method TEXT NOT NULL CHECK (method IN ('especes','virement','cheque')),
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes > 0),
    cash_session_id INTEGER REFERENCES cash_sessions(id),
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_purchase_receipts_supplier ON purchase_receipts(supplier);
CREATE INDEX idx_supplier_payments_receipt ON supplier_payments(receipt_id);
"""

MIGRATION_4 = """
ALTER TABLE installments ADD COLUMN cancelled_centimes INTEGER NOT NULL DEFAULT 0
    CHECK (cancelled_centimes >= 0 AND cancelled_centimes <= amount_centimes);

CREATE TABLE sale_returns (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    customer_id INTEGER REFERENCES customers(id),
    total_centimes INTEGER NOT NULL CHECK (total_centimes >= 0),
    debt_reduction_centimes INTEGER NOT NULL CHECK (debt_reduction_centimes >= 0),
    refund_centimes INTEGER NOT NULL CHECK (refund_centimes >= 0),
    refund_method TEXT NOT NULL CHECK (refund_method IN ('especes','avoir')),
    session_id INTEGER REFERENCES cash_sessions(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_sale_returns_sale ON sale_returns(sale_id);

CREATE TABLE sale_return_items (
    id INTEGER PRIMARY KEY,
    return_id INTEGER NOT NULL REFERENCES sale_returns(id),
    sale_item_id INTEGER NOT NULL REFERENCES sale_items(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes >= 0),
    restocked INTEGER NOT NULL CHECK (restocked IN (0,1)),
    UNIQUE (return_id, sale_item_id)
);
CREATE INDEX idx_sale_return_items_sale_item ON sale_return_items(sale_item_id);

CREATE TABLE sale_return_serials (
    return_item_id INTEGER NOT NULL REFERENCES sale_return_items(id),
    serial_id INTEGER NOT NULL REFERENCES product_serials(id),
    PRIMARY KEY (return_item_id, serial_id)
);

CREATE TABLE customer_credit_movements (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes <> 0),
    return_id INTEGER REFERENCES sale_returns(id),
    sale_id INTEGER REFERENCES sales(id),
    created_at TEXT NOT NULL,
    CHECK ((return_id IS NOT NULL AND sale_id IS NULL) OR
           (return_id IS NULL AND sale_id IS NOT NULL))
);
CREATE INDEX idx_customer_credit_customer ON customer_credit_movements(customer_id);
"""

MIGRATION_5 = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('caissier','responsable','administrateur')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
ALTER TABLE audit_log ADD COLUMN actor_user_id INTEGER REFERENCES users(id);
"""

MIGRATION_6 = """
ALTER TABLE sale_items ADD COLUMN unit_cost_centimes INTEGER
    CHECK (unit_cost_centimes IS NULL OR unit_cost_centimes >= 0);
"""

MIGRATION_7 = """
CREATE TABLE suppliers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT,
    email TEXT,
    address TEXT,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
ALTER TABLE purchase_receipts ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id);
INSERT INTO suppliers (name, created_at, updated_at)
SELECT MIN(supplier), MIN(created_at), MAX(created_at)
FROM purchase_receipts
GROUP BY supplier COLLATE NOCASE;
UPDATE purchase_receipts
SET supplier_id = (
    SELECT id FROM suppliers
    WHERE suppliers.name = purchase_receipts.supplier COLLATE NOCASE
);
CREATE INDEX idx_purchase_receipts_supplier_id ON purchase_receipts(supplier_id);
"""

MIGRATION_8 = """
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    customer_id INTEGER REFERENCES customers(id),
    recipient_name TEXT NOT NULL,
    recipient_phone TEXT,
    recipient_address TEXT,
    discount_centimes INTEGER NOT NULL CHECK (discount_centimes >= 0),
    total_centimes INTEGER NOT NULL CHECK (total_centimes >= 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','converted','cancelled')),
    created_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    converted_sale_id INTEGER UNIQUE REFERENCES sales(id)
);
CREATE TABLE quote_items (
    id INTEGER PRIMARY KEY,
    quote_id INTEGER NOT NULL REFERENCES quotes(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    reference TEXT NOT NULL,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_centimes INTEGER NOT NULL CHECK (unit_price_centimes >= 0),
    tax_rate_bp INTEGER NOT NULL CHECK (tax_rate_bp >= 0),
    total_centimes INTEGER NOT NULL CHECK (total_centimes >= 0)
);
CREATE INDEX idx_quote_items_quote ON quote_items(quote_id);
ALTER TABLE sales ADD COLUMN quote_id INTEGER REFERENCES quotes(id);
CREATE UNIQUE INDEX idx_sales_quote ON sales(quote_id) WHERE quote_id IS NOT NULL;
"""

MIGRATION_9 = """
CREATE TABLE product_suppliers (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (product_id, supplier_id)
);
CREATE INDEX idx_product_suppliers_supplier ON product_suppliers(supplier_id, product_id);
"""

MIGRATION_10 = """
ALTER TABLE sales ADD COLUMN loyalty_points_redeemed INTEGER NOT NULL DEFAULT 0
    CHECK (loyalty_points_redeemed >= 0);

CREATE TABLE loyalty_movements (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    points INTEGER NOT NULL CHECK (points <> 0),
    kind TEXT NOT NULL CHECK (kind IN
        ('earned','redeemed','return_reversal','return_restore')),
    sale_id INTEGER REFERENCES sales(id),
    return_id INTEGER REFERENCES sale_returns(id),
    source_movement_id INTEGER REFERENCES loyalty_movements(id),
    expires_on TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_loyalty_customer_expiry
    ON loyalty_movements(customer_id, expires_on, id);
CREATE INDEX idx_loyalty_sale_kind ON loyalty_movements(sale_id, kind);
CREATE INDEX idx_loyalty_source_kind
    ON loyalty_movements(source_movement_id, kind);
"""

MIGRATION_11 = """
ALTER TABLE purchase_receipts ADD COLUMN status TEXT NOT NULL DEFAULT 'validee'
    CHECK (status IN ('validee','annulee'));
ALTER TABLE purchase_receipt_items ADD COLUMN unit_price_centimes INTEGER
    NOT NULL DEFAULT 0 CHECK (unit_price_centimes >= 0);
UPDATE purchase_receipt_items
SET unit_price_centimes = (
    SELECT price_centimes FROM products
    WHERE products.id = purchase_receipt_items.product_id
);
"""

MIGRATION_12 = """
CREATE TABLE supplier_adjustments (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    receipt_id INTEGER NOT NULL REFERENCES purchase_receipts(id),
    amount_centimes INTEGER NOT NULL CHECK (amount_centimes <> 0),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_supplier_adjustments_supplier
    ON supplier_adjustments(supplier_id);
"""

def migration_13(conn):
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(suppliers)")
    }
    if "opening_balance_centimes" not in columns:
        conn.execute(
            "ALTER TABLE suppliers ADD COLUMN "
            "opening_balance_centimes INTEGER NOT NULL DEFAULT 0"
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS supplier_opening_payments (
            id INTEGER PRIMARY KEY,
            supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
            method TEXT NOT NULL CHECK (method IN ('especes','virement','cheque')),
            amount_centimes INTEGER NOT NULL CHECK (amount_centimes > 0),
            cash_session_id INTEGER REFERENCES cash_sessions(id),
            note TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_supplier_opening_payments_supplier
        ON supplier_opening_payments(supplier_id)
    """)
def migration_14(conn):
    for table in ("customers", "payment_plans"):
        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if "guarantor" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN guarantor TEXT")
MIGRATIONS = [
    MIGRATION_1, MIGRATION_2, MIGRATION_3, MIGRATION_4, MIGRATION_5, MIGRATION_6,
    MIGRATION_7, MIGRATION_8, MIGRATION_9, MIGRATION_10, MIGRATION_11, MIGRATION_12,
    migration_13, migration_14
]


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO warehouses (code, name) VALUES ('WH-001', 'Entrepôt Principal')")
    conn.execute("INSERT INTO cash_registers (code, name) VALUES ('REG-001', 'Caisse Principale')")
    # Réglage « vente avec stock insuffisant » : warn (avertir), block (bloquer) ou allow (autoriser)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('sale_when_insufficient', 'warn')")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('default_tax_rate_bp', '1900')")


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise sqlite3.DatabaseError(
            "La base de données a été créée par une version plus récente du logiciel."
        )
    for number, script in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue

        if callable(script):
            conn.execute("BEGIN")
            try:
                script(conn)
                conn.execute(f"PRAGMA user_version = {number}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        else:
            conn.executescript(
                "BEGIN;\n" + script
                + f"\nPRAGMA user_version = {number};\nCOMMIT;"
            )

        if number == 1:
            _seed(conn)


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Ouvre (et crée si besoin) la base. Les transactions sont gérées par transaction()."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Tout ou rien. Peut être imbriquée : seule la plus extérieure valide vraiment."""
    name = f"sp_{next(_savepoint_ids)}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield conn
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    else:
        conn.execute(f"RELEASE {name}")


def log_audit(conn, action: str, entity: str | None = None, entity_id: int | None = None,
              details: str | None = None, at: str | None = None,
              actor_user_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, action, entity, entity_id, details, actor_user_id) "
        "VALUES (?,?,?,?,?,?)",
        (at or now_iso(), action, entity, entity_id, details, actor_user_id),
    )