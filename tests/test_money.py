import unittest

from magasin.money import (add_tax, da_to_centimes, format_da, margin_percent, markup_percent,
                           split_evenly, split_ttc)


class MoneyTests(unittest.TestCase):
    def test_conversion_et_format(self):
        self.assertEqual(da_to_centimes("38 000,00"), 3_800_000)
        self.assertEqual(da_to_centimes(38000), 3_800_000)
        self.assertEqual(da_to_centimes("0,5"), 50)
        self.assertEqual(format_da(3_900_000), "39 000,00 DA")
        self.assertEqual(format_da(-100_000), "-1 000,00 DA")
        self.assertEqual(format_da(5), "0,05 DA")

    def test_mensualites_exactes(self):
        reste = 2_800_000  # 28 000,00 DA
        self.assertEqual(split_evenly(reste, 8), [350_000] * 8)
        self.assertEqual(split_evenly(reste, 10), [280_000] * 10)

    def test_la_derniere_mensualite_absorbe_l_arrondi(self):
        reste = 2_800_000
        neuf = split_evenly(reste, 9)
        self.assertEqual(neuf, [311_111] * 8 + [311_112])
        six = split_evenly(reste, 6)
        self.assertEqual(six, [466_667] * 5 + [466_665])
        for parts in range(1, 25):
            self.assertEqual(sum(split_evenly(reste, parts)), reste)

    def test_achat_ttc(self):
        # 5 appareils à 30 000 DA TTC = 150 000 DA TTC (cas de l'essai)
        ht, tva = split_ttc(15_000_000, 1900)
        self.assertEqual((ht, tva), (12_605_042, 2_394_958))
        self.assertEqual(format_da(ht), "126 050,42 DA")
        self.assertEqual(format_da(tva), "23 949,58 DA")
        # le prix unitaire HT multiplié par 5 s'écarte de 2 centimes du total exact
        unitaire_ht, _ = split_ttc(3_000_000, 1900)
        self.assertEqual(format_da(unitaire_ht), "25 210,08 DA")
        self.assertEqual(ht - 5 * unitaire_ht, 2)

    def test_vente_ttc(self):
        ht, tva = split_ttc(3_900_000, 1900)
        self.assertEqual(format_da(ht), "32 773,11 DA")
        self.assertEqual(format_da(tva), "6 226,89 DA")
        self.assertEqual(ht + tva, 3_900_000)
        self.assertEqual(add_tax(ht, 1900)[1], ht + add_tax(ht, 1900)[0])

    def test_majoration_et_marge_ne_sont_pas_la_meme_chose(self):
        self.assertEqual(str(markup_percent(3_000_000, 3_900_000)), "30.00")
        self.assertEqual(str(margin_percent(3_000_000, 3_900_000)), "23.08")


if __name__ == "__main__":
    unittest.main()
