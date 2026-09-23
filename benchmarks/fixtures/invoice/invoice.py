from decimal import Decimal


def invoice_total(prices, tax_rate):
    subtotal = sum((Decimal(str(price)) for price in prices), start=Decimal(0))
    tax = subtotal * Decimal(str(tax_rate))
    return subtotal + tax + tax
