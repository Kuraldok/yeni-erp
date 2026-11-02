"""Main product display derives from explicit upper-product relationships."""
from django.db.models import Case, CharField, F, Min, Q, Value, When
from django.db.models.functions import Coalesce

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


def manual_stock_products(products):
    """Exclude typed and source-labelled semi-finished cards from manual entry."""
    from .routing import is_semifinished_component
    lines = MaterialRequirement.objects.filter(component__kind__in=['', 'finished'])
    kinds = Q(pk__in=[])
    for kind, sheet in lines.order_by().values_list('component_kind', 'sheet').distinct():
        if is_semifinished_component(kind, sheet):
            kinds |= Q(component_kind=kind, sheet=sheet)
    source_ids = lines.filter(kinds).exclude(
        ~Q(component_code='') & Q(component_code=F('parent_code')),
    ).values('component_id')
    return products.exclude(kind='semi').exclude(pk__in=source_ids)


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


def order_product_cards(products):
    """Use the first displayed group, including source groups on unclassified cards."""
    return products.annotate(
        source_group=Min('materialrecipe__sheet', filter=~Q(materialrecipe__sheet='')),
    ).annotate(
        first_group=Case(
            When(category='', then=Coalesce('source_group', Value(''))),
            When(source_group__isnull=True, then=F('category')),
            When(category__lte=F('source_group'), then=F('category')),
            default=F('source_group'), output_field=CharField(),
        ),
    ).order_by('first_group', 'code', 'pk')
