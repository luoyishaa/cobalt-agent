class Pricing:
    def __init__(self, catalog):
        self.catalog = catalog
        self._cache = {}

    def quote(self, sku, quantity):
        key = sku.strip().lower()
        if key not in self._cache:
            self._cache[key] = self.catalog.price(key)
        return self._cache[key] * quantity

    def invalidate(self, sku):
        self._cache.pop(sku, None)
