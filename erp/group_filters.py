"""Product-group membership shared by lists, reports and dashboard summaries."""
from django.core.exceptions import PermissionDenied
from django.db.models import Q, CharField
from django.db.models.functions import Cast

from .models import (
    CatalogImport, MaterialRecipe, MaterialRequirement, Order, OrderLine, Partner,
    Product, ProductionFlow, ProductionJob, ProductionRoute, RouteStep,
    StockMovement, Warehouse,
    AuditEvent, QuantityReview, ConsumptionRecipe, ConsumptionRecipeLine, ProductionPosting,
)


FILTER_PERMISSIONS = {
    Product: (), MaterialRecipe: (), MaterialRequirement: (),
    StockMovement: ('view_product',), OrderLine: ('view_product',),
    Order: ('view_product', 'view_orderline'),
    Warehouse: ('view_product', 'view_stockmovement'),
    Partner: ('view_product', 'view_order', 'view_orderline'),
    ProductionRoute: ('view_product',), RouteStep: ('view_product', 'view_productionroute'),
    ProductionJob: ('view_product',),
    ProductionFlow: ('view_product', 'view_productionroute', 'view_productionjob'),
    CatalogImport: ('view_materialrecipe', 'view_materialrequirement'),
    ConsumptionRecipe: ('view_product',), ConsumptionRecipeLine: ('view_product','view_consumptionrecipe'),
    ProductionPosting: ('view_product','view_productionjob'), AuditEvent: (), QuantityReview: (),
}


def can_filter_groups(user, model):
    return all(user.has_perm('erp.' + code) for code in FILTER_PERMISSIONS[model])


def available_groups(user):
    groups = set()
    if user.has_perm('erp.view_product'):
        groups.update(Product.objects.exclude(category='').values_list('category', flat=True))
        # Source groups also appear in the product's existing information screen.
        groups.update(MaterialRecipe.objects.filter(product__isnull=False).exclude(sheet='').values_list('sheet', flat=True))
        groups.update(MaterialRequirement.objects.filter(component__isnull=False).exclude(sheet='').values_list('sheet', flat=True))
    if user.has_perm('erp.view_materialrecipe'):
        groups.update(MaterialRecipe.objects.exclude(sheet='').values_list('sheet', flat=True))
    if user.has_perm('erp.view_materialrequirement'):
        groups.update(MaterialRequirement.objects.exclude(sheet='').values_list('sheet', flat=True))
    return sorted(groups)


def grouped_records(model, category=None):
    """None means any named group; use subqueries so joins cannot duplicate rows."""
    source = Q(sheet=category) if category is not None else ~Q(sheet='')
    if model is Product:
        own = Q(category=category) if category is not None else ~Q(category='')
        return Product.objects.filter(
            own
            | Q(pk__in=MaterialRecipe.objects.filter(source).values('product_id'))
            | Q(pk__in=MaterialRequirement.objects.filter(source).values('component_id'))
        )
    products = grouped_records(Product, category)
    if model is MaterialRecipe:
        return MaterialRecipe.objects.filter(source | Q(sheet='', product__in=products))
    if model is MaterialRequirement:
        recipes = grouped_records(MaterialRecipe, category)
        return MaterialRequirement.objects.filter(
            source | (Q(sheet='') & (Q(recipe__in=recipes) | Q(component__in=products)))
        )
    if model in (StockMovement, OrderLine, ProductionRoute, ProductionJob):
        return model.objects.filter(product__in=products)
    if model is Order:
        return Order.objects.filter(pk__in=OrderLine.objects.filter(product__in=products,status='active').values('order_id'))
    if model is Warehouse:
        return Warehouse.objects.filter(pk__in=StockMovement.objects.filter(product__in=products).values('warehouse_id'))
    if model is Partner:
        return Partner.objects.filter(pk__in=grouped_records(Order, category).values('partner_id'))
    if model is RouteStep:
        return RouteStep.objects.filter(route__in=grouped_records(ProductionRoute, category))
    if model is ProductionFlow:
        return ProductionFlow.objects.filter(
            Q(pk__in=ProductionRoute.objects.filter(product__in=products).values('flow_id'))
            | Q(pk__in=ProductionJob.objects.filter(product__in=products).values('flow_id'))
        )
    if model is CatalogImport:
        return CatalogImport.objects.filter(
            Q(pk__in=grouped_records(MaterialRecipe, category).values('source_id'))
            | Q(pk__in=grouped_records(MaterialRequirement, category).values('source_id'))
        )
    if model is ConsumptionRecipe:
        return model.objects.filter(product__in=products)
    if model is ConsumptionRecipeLine:
        return model.objects.filter(recipe__in=grouped_records(ConsumptionRecipe,category))
    if model is ProductionPosting:
        return model.objects.filter(job__in=grouped_records(ProductionJob,category))
    if model in (AuditEvent,QuantityReview):
        matches = Q(pk__in=[])
        for target in (Product,Warehouse,StockMovement,Partner,Order,OrderLine,ProductionRoute,RouteStep,
                MaterialRecipe,MaterialRequirement,CatalogImport,ProductionFlow,ProductionJob,ConsumptionRecipe,ConsumptionRecipeLine,ProductionPosting):
            identities = grouped_records(target,category).annotate(identity=Cast('pk',CharField())).values('identity')
            matches |= Q(model_label=target._meta.label_lower,object_id__in=identities)
        return model.objects.filter(matches)
    raise ValueError(f'Unsupported group filter model: {model.__name__}')


def filter_by_group(queryset, category, user):
    if not category:
        return queryset
    if not can_filter_groups(user, queryset.model):
        raise PermissionDenied('Bu kayıtları ürün grubuna göre süzmek için ilişkili kayıtları görüntüleme izni gerekiyor.')
    if category == '__none__':
        return queryset.exclude(pk__in=grouped_records(queryset.model).values('pk'))
    return queryset.filter(pk__in=grouped_records(queryset.model, category).values('pk'))
