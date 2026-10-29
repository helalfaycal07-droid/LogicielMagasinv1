"""Données fictives pour la démonstration (produits et IMEI inventés, aucune donnée réelle)."""

from .cash import open_session
from .customers import create_customer
from .products import create_product
from .sales import Line, PaymentIn, create_sale
from .serials import add_units, complete_imei
from .stock import add_movement


def remplir_demo(conn) -> None:
    create_product(conn, "101", "TV BRANDT 40 GOOGLE TV", 3_900_000, cost_centimes=3_000_000,
                   category="Téléviseurs", brand="Brandt", initial_stock=3, min_stock=2)
    create_product(conn, "102", "Téléviseur 32 pouces", 2_450_000, cost_centimes=1_900_000,
                   category="Téléviseurs", initial_stock=0, min_stock=1)
    create_product(conn, "103", "Réfrigérateur 350 L", 5_800_000, cost_centimes=4_600_000,
                   category="Froid", initial_stock=6, min_stock=2, barcode="6130000000103")
    create_product(conn, "104", "Câble USB-C 1 m", 80_000, cost_centimes=30_000,
                   category="Accessoires", initial_stock=40, min_stock=10)
    machine = create_product(conn, "105", "Machine à laver 7 kg", 4_200_000, cost_centimes=3_400_000,
                             category="Électroménager", initial_stock=1, min_stock=1)
    # vendue une fois de trop : le stock négatif doit rester visible
    add_movement(conn, machine, -2, "vente", ref_type="Sale", ref_number="VTE-DEMO", note="Démonstration")

    # Smartphones suivis par IMEI1 + IMEI2 (numéros fictifs avec chiffre de contrôle valide)
    telephone = create_product(conn, "106", "Smartphone Test 128 Go", 3_200_000, cost_centimes=2_600_000,
                               category="Téléphones", brand="Marque Test", min_stock=1, track_serial=True)
    appareils = [(complete_imei(f"35899900{n:06d}"), complete_imei(f"35899900{n + 500:06d}")) for n in (1, 2, 3)]
    add_units(conn, telephone, appareils)
    open_session(conn, opening_centimes=0)
    client = create_customer(conn, "Client Démo", "0555000000")
    create_sale(conn, [Line(telephone, 1, imeis=[appareils[0][0]])], [PaymentIn("especes", 3_200_000)],
                customer_id=client)
