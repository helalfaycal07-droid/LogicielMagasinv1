import unittest

from magasin.customers import normalize_phone
from magasin.errors import ValidationError


class NormalisationTelephoneTests(unittest.TestCase):
    def test_mobile_algerien_accepte_les_formats_courants(self):
        variantes = (
            "0555000000",
            "05 55 00 00 00",
            "05/55/00/00/00",
            "+213 555 00 00 00",
            "+213 0555 00 00 00",
            "00213 555 00 00 00",
            "213 555 00 00 00",
        )
        for numero in variantes:
            with self.subTest(numero=numero):
                self.assertEqual(normalize_phone(numero), "0555000000")

    def test_fixe_algerien_local_et_international(self):
        self.assertEqual(normalize_phone("021 23 45 67"), "021234567")
        self.assertEqual(normalize_phone("+213 21 23 45 67"), "021234567")

    def test_numero_invalide_reste_refuse(self):
        for numero in ("055500000", "05550000000", "05AB000000"):
            with self.subTest(numero=numero), self.assertRaises(ValidationError):
                normalize_phone(numero)


if __name__ == "__main__":
    unittest.main()
