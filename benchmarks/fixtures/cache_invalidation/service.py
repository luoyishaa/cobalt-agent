from catalog import Catalog
from pricing import Pricing


class Store:
    def __init__(self, prices):
        self.catalog = Catalog(prices)
        self.pricing = Pricing(self.catalog)

    def quote(self, sku, quantity):
        return self.pricing.quote(sku, quantity)

    def change_price(self, sku, price):
        self.catalog.update(sku, price)
        self.pricing.invalidate(sku)
