"""Main product display derives from explicit upper-product relationships."""
from django.db.models import Q

from .models import MaterialRecipe, MaterialRequirement, Product


def main_products():
    upper_ids = MaterialRecipe.objects.filter(product__isnull=False).values('product_id')
    component_ids = MaterialRequirement.objects.filter(component__isnull=False).values('component_id')
    # Standalone manually created cards remain visible before a recipe is added.
    # A finished component does not become a main product solely from its kind.
    return Product.objects.exclude(kind__in=['semi', 'raw', 'part']).filter(
        Q(pk__in=upper_ids)
        | (Q(kind__in=['', 'finished']) & ~Q(pk__in=component_ids))
    )


def product_groups(products):
    groups = set(products.exclude(category='').values_list('category', flat=True))
    groups.update(
        MaterialRecipe.objects.filter(product__in=products)
        .exclude(sheet='')
        .values_list('sheet', flat=True)
    )
    return sorted(groups)


def filter_group(products, category):
    if category == '__none__':
        grouped_ids = MaterialRecipe.objects.exclude(sheet='').filter(product__isnull=False).values('product_id')
        return products.filter(category='').exclude(pk__in=grouped_ids)
    if category:
        upper_ids = MaterialRecipe.objects.filter(sheet=category, product__isnull=False).values('product_id')
        return products.filter(Q(category=category) | Q(pk__in=upper_ids))
    return products
