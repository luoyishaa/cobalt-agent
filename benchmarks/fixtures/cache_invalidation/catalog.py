class Catalog:
    def __init__(self, prices):
        self._prices = {sku.strip().lower(): price for sku, price in prices.items()}

    def price(self, sku):
        return self._prices[sku.strip().lower()]

    def update(self, sku, price):
        self._prices[sku.strip().lower()] = price
