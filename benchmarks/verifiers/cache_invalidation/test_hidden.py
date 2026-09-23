import unittest

from service import Store


class CacheVerifier(unittest.TestCase):
    def test_repeated_price_changes_after_mixed_case_reads(self):
        store = Store({"A-1": 2})
        self.assertEqual(store.quote(" A-1 ", 3), 6)
        store.change_price("a-1", 4)
        self.assertEqual(store.quote("A-1", 3), 12)
        store.change_price(" A-1 ", 5)
        self.assertEqual(store.quote("a-1", 3), 15)

    def test_other_sku_is_unaffected(self):
        store = Store({"A-1": 2, "B-2": 7})
        store.quote("B-2", 1)
        store.change_price(" a-1 ", 4)
        self.assertEqual(store.quote("B-2", 2), 14)
