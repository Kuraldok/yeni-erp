"""Shared product eligibility and selection data for both order-entry screens."""
from .models import MaterialRecipe, MaterialRequirement, Product
from .products import main_products, order_product_cards


def selection_pk(value):
    text = str(value or '')
    if not text.isascii() or not text.isdigit() or len(text) > 19:
        return None
    number = int(text)
    return number if 0 < number <= 9223372036854775807 else None


def order_products(kind, include_inactive=False):
    if kind == 'sale':
        return order_product_cards(main_products() if include_inactive else main_products().filter(active=True))
    if kind == 'purchase':
        products = Product.objects.filter(kind='raw')
        return (products if include_inactive else products.filter(active=True)).order_by('category', 'code', 'pk')
    return Product.objects.none()


def product_selection(kind):
    products = list(order_products(kind))
    groups = {product.pk: set() for product in products}
    for product in products:
        if product.category:
            groups[product.pk].add(product.category)
    for product_id, sheet in MaterialRecipe.objects.filter(product_id__in=groups).exclude(sheet='').values_list('product_id', 'sheet'):
        groups[product_id].add(sheet)
    for product_id, sheet in MaterialRequirement.objects.filter(component_id__in=groups).exclude(sheet='').values_list('component_id', 'sheet'):
        groups[product_id].add(sheet)
    return {
        'kind': kind,
        'groups': sorted({group for names in groups.values() for group in names}),
        'products': [{
            'id': product.pk, 'label': str(product),
            'unit': product.get_unit_display() or 'Birim belirtilmedi',
            'groups': sorted(groups[product.pk]),
        } for product in products],
    }
