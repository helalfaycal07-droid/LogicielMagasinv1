"""Outils communs aux tests : base en mémoire, produits et clients de démonstration."""

from magasin.cash import open_session
from magasin.customers import create_customer
from magasin.db import connect
from magasin.products import create_product

TV_PRICE = 3_900_000   # 39 000,00 DA
TV_COST = 3_000_000    # 30 000,00 DA


def new_db():
    return connect(":memory:")


def tv(conn, stock=3):
    return create_product(conn, "101", "TV BRANDT 40 GOOGLE TV", TV_PRICE,
                          cost_centimes=TV_COST, initial_stock=stock)


def credit_customer(conn, name="Client Test", **kwargs):
    return create_customer(conn, name, "0555000000", credit_allowed=True, **kwargs)


def open_cash(conn, opening=0):
    return open_session(conn, opening_centimes=opening)
