import unittest
from decimal import Decimal

from invoice import invoice_total


class InvoiceTests(unittest.TestCase):
    def test_total_with_tax(self):
        self.assertEqual(invoice_total(["10.00", "20.00"], "0.10"), Decimal("33.0000"))

    def test_zero_tax(self):
        self.assertEqual(invoice_total(["4.00"], "0"), Decimal("4.00"))
