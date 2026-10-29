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

MIGRATIONS = [MIGRATION_1, MIGRATION_2]


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO warehouses (code, name) VALUES ('WH-001', 'Entrepôt Principal')")
    conn.execute("INSERT INTO cash_registers (code, name) VALUES ('REG-001', 'Caisse Principale')")
    # Réglage « vente avec stock insuffisant » : warn (avertir), block (bloquer) ou allow (autoriser)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('sale_when_insufficient', 'warn')")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('default_tax_rate_bp', '1900')")


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue
        conn.executescript("BEGIN;\n" + script + f"\nPRAGMA user_version = {number};\nCOMMIT;")
        if number == 1:
            _seed(conn)


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Ouvre (et crée si besoin) la base. Les transactions sont gérées par transaction()."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


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
              details: str | None = None, at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, action, entity, entity_id, details) VALUES (?,?,?,?,?)",
        (at or now_iso(), action, entity, entity_id, details),
    )
