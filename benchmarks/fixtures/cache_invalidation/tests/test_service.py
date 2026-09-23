import unittest

from service import Store


class StoreTests(unittest.TestCase):
    def test_quotes_normalize_sku(self):
        store = Store({"A-1": 10})
        self.assertEqual(store.quote(" a-1 ", 2), 20)

    def test_price_change_invalidates_cached_quote_for_mixed_case_sku(self):
        store = Store({"A-1": 10})
        self.assertEqual(store.quote("a-1", 2), 20)
        store.change_price(" A-1 ", 12)
        self.assertEqual(store.quote("a-1", 2), 24)

    def test_price_change_keeps_other_cached_skus(self):
        store = Store({"A-1": 10, "B-2": 5})
        self.assertEqual(store.quote("b-2", 3), 15)
        store.change_price("A-1", 12)
        self.assertEqual(store.quote("b-2", 3), 15)


if __name__ == "__main__":
    unittest.main()
