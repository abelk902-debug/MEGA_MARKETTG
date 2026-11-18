class ServiceError(Exception):
    """Business-rule error. `message` is safe to show to the user (Arabic)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CheckoutError(ServiceError):
    pass


class CartError(ServiceError):
    pass


class StoreError(ServiceError):
    pass


class ProductError(ServiceError):
    pass


class ValidationError(ProductError):
    """Bad user input (code starts with 'invalid_' / 'bad_' / 'too_')."""


class CategoryError(ServiceError):
    pass

class OrderError(ServiceError):
    """Order management business-rule error."""
    pass