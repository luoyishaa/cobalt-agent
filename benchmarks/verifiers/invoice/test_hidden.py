import unittest
from decimal import Decimal

from invoice import invoice_total


class InvoiceVerifier(unittest.TestCase):
    def test_fractional_values(self):
        self.assertEqual(invoice_total(["0.10", "0.20"], "0.10"), Decimal("0.3300"))

    def test_empty_invoice(self):
        self.assertEqual(invoice_total([], "0.20"), Decimal("0.00"))
