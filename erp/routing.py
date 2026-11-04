"""Explicit category rules for component production and procurement routes."""
from collections import defaultdict, Counter
import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q

from .models import MaterialRecipe, MaterialRequirement, Product, ProductionFlow, ProductionRoute, RouteStep
from .workflows import ensure_workflows

# Keep the existing identity so repeated setup preserves routes and custom steps.
RULE_KEY = 'fountain-bench-v1'
RAW_RULE_KEY = 'raw-material-purchase-v1'


def add_route_warning(result, message, *, line=None, product=None):
    categories = {line.sheet} if line and line.sheet else set()
    if product:
        if product.category:
            categories.add(product.category)
        categories.update(MaterialRequirement.objects.filter(component=product).exclude(sheet='').values_list('sheet', flat=True))
        categories.update(MaterialRecipe.objects.filter(product=product).exclude(sheet='').values_list('sheet', flat=True))
    result['skipped'].append(message)
    result.setdefault('warnings', []).append({'message': message, 'categories': sorted(categories),
        'product_id': product.pk if product else line.component_id if line else None,
        'code': product.code if product else line.component_code if line else '',
        'sheet': line.sheet if line else '', 'row': line.row_number if line else None})


def normalized(value):
    return ' '.join(unicodedata.normalize('NFC', value or '').casefold().replace('i\u0307', 'i').replace('ı', 'i').split())


CASTING_CATEGORIES = {normalized(name) for name in (
    'Şömine', 'Şömineler',
    'Posta kutusu', 'Posta kutuları', 'Soba', 'Sobalar',
    'Sandalye', 'Sandalyeler', 'Kapak', 'Kapaklar',
    'Bariyer', 'Bariyerler', 'Ferforje korkuluk', 'Ferforje korkuluklar',
    'Masa', 'Masalar', 'Masa ayağı', 'Masa ayakları',
    'Masa ve masa ayakları', 'Masalar ve masa ayakları',
    'Döküm saksı', 'Döküm saksılar', 'Döküm saksıları',
)}
OUTDOOR_WOOD_CATEGORIES = {normalized(name) for name in (
    'Kamelya', 'Kamelyalar', 'Piknik masası', 'Piknik masaları',
    'Kamelya ve piknik masası', 'Kamelya ve piknik masaları',
    'Kamelyalar ve piknik masası', 'Kamelyalar ve piknik masaları',
)}
WOOD_CATEGORIES = {normalized(name) for name in (
    'Ahşap saksı', 'Ahşap saksılar', 'Ahşap saksıları',
)} | OUTDOOR_WOOD_CATEGORIES
MANGAL_CATEGORIES = {normalized(name) for name in ('Mangal', 'Mangallar')}
MANGAL_WOOD_COMPONENTS = {normalized(name) for name in (
    'Ahşap saplı kulp', 'Tel ızgara sapı',
)}
GRILL_CATEGORIES = {normalized(name) for name in ('Izgara', 'Izgaralar')}
SEHPA_CATEGORIES = {normalized(name) for name in ('Sehpa', 'Sehpalar')}
SEHPA_WOOD_COMPONENTS = {normalized('Masa tablası')}


def is_grill_category(category):
    return normalized(category) in GRILL_CATEGORIES


def main_product_method(category):
    if is_grill_category(category):
        return 'casting'
    if normalized(category) in OUTDOOR_WOOD_CATEGORIES:
        return 'wood'
    return None


def is_codeless_grill_material(kind, category, code):
    return is_grill_category(category) and normalized(kind) == 'hammadde' and not code


def requirement_issues(line):
    """Keep historical source notes stored; omit the now-intentional blank code."""
    if is_codeless_grill_material(line.component_kind, line.sheet, line.component_code):
        return [issue for issue in line.issues if issue != 'Bileşen Kodu boş.']
    return list(line.issues)


def accepts_finished_components(category):
    """Only the newly specified categories treat a Mamul component as semi-finished."""
    return normalized(category) in CASTING_CATEGORIES | WOOD_CATEGORIES | MANGAL_CATEGORIES | SEHPA_CATEGORIES


def is_semifinished_component(kind, category):
    kind = normalized(kind)
    return kind == 'yari mamul' or (kind == 'mamul' and accepts_finished_components(category))


def component_method(line):
    if is_grill_category(line.sheet):
        return None
    if not is_semifinished_component(line.component_kind, line.sheet):
        return None
    if line.component_code and line.component_code == line.parent_code:
        return None
    category = normalized(line.sheet)
    if category in CASTING_CATEGORIES:
        return 'casting'
    if category in WOOD_CATEGORIES:
        return 'wood'
    if category in SEHPA_CATEGORIES:
        name = normalized(line.component_name)
        if not name:
            return None
        return 'wood' if name in SEHPA_WOOD_COMPONENTS else 'casting'
    if category in MANGAL_CATEGORIES:
        name = normalized(line.component_name)
        if not name:
            return None
        return 'wood' if name in MANGAL_WOOD_COMPONENTS else 'casting'
    if category in ('çeşme', 'çeşmeler'):
        return 'casting'
    if category in ('banklar', 'park bahçe banki'):
        name = normalized(line.component_name)
        if not name:
            return None
        if name in ('taşiyici ayak', 'gövde'):
            return 'casting'
        if name == 'ahşap bölüm':
            return 'wood'
        return 'purchase'
    return None


@transaction.atomic
def apply_component_routes(source=None):
    from .products import main_products
    lines = list(MaterialRequirement.objects.select_related('component').all())
    main_ids = main_products().values('pk')
    route_card_ids = Product.objects.filter(Q(pk__in=main_ids) | Q(kind='semi')).values('pk')
    direct_methods = defaultdict(set)
    for pk, kind, category in Product.objects.filter(pk__in=route_card_ids).values_list('pk', 'kind', 'category'):
        method = main_product_method(category)
        if method and (kind != 'semi' or method == 'wood'):
            direct_methods[pk].add(method)
    direct_recipes = []
    for recipe in MaterialRecipe.objects.filter(product_id__in=route_card_ids).select_related('product'):
        method = main_product_method(recipe.sheet)
        if method and (recipe.product.kind != 'semi' or method == 'wood'):
            direct_methods[recipe.product_id].add(method)
            direct_recipes.append(recipe)
    affected = None
    if source is not None:
        affected = {line.component_id for line in lines if line.source_id==source.pk and component_method(line)}
        affected.update(recipe.product_id for recipe in direct_recipes if recipe.source_id == source.pk)
    plans = defaultdict(set)
    direct_products = set(direct_methods) if affected is None else set(direct_methods) & affected
    for product_id in direct_products:
        plans[product_id].update(direct_methods[product_id])
    finished_components = set()
    warning_result = {'skipped': [], 'warnings': []}
    missing = warning_result['skipped']
    for line in lines:
        category = normalized(line.sheet)
        if is_semifinished_component(line.component_kind, line.sheet) and not normalized(line.component_name) and category in {'banklar', 'park bahçe banki'} | MANGAL_CATEGORIES | SEHPA_CATEGORIES:
            if source is None or line.source_id==source.pk:
                add_route_warning(warning_result, f'{line.sheet}:{line.row_number}: bileşen adı boş; üretim/temin yolu atanmadı.', line=line)
            continue
        method = component_method(line)
        if not method or (affected is not None and line.component_id not in affected):
            continue
        if not line.component_id:
            add_route_warning(warning_result, f'{line.sheet}:{line.row_number}: bileşen kartı eşleştirilmemiş.', line=line)
            continue
        plans[line.component_id].add(method)
        if accepts_finished_components(line.sheet):
            finished_components.add(line.component_id)
    if any(methods & {'wood', 'casting'} for methods in plans.values()):
        ensure_workflows()
    flows = {flow.code: flow for flow in ProductionFlow.objects.filter(code__in=['wood','casting'])}
    result = {'created': 0, 'existing': 0, 'assignments': {}, 'skipped': missing, 'warnings': warning_result['warnings']}
    counts = Counter()
    for product_id, methods in sorted(plans.items()):
        product = Product.objects.select_for_update().get(pk=product_id)
        direct_mamul = product_id in direct_products and product.kind in ('', 'finished')
        if product.kind!='semi' and not direct_mamul and not (product.kind=='finished' and product_id in finished_components):
            add_route_warning(result, f'{product.code}: kartın türü yarı mamul değil; değiştirilmedi.', product=product)
            continue
        if len(methods)!=1:
            add_route_warning(result, f'{product.code}: kategoriler arasında farklı rota kuralları var; değiştirilmedi.', product=product)
            continue
        method = next(iter(methods))
        supply = 'purchase' if method=='purchase' else 'manufacture'
        area = '' if method=='purchase' else method
        if (product.supply_method and product.supply_method!=supply) or (product.production_area and product.production_area!=area):
            add_route_warning(result, f'{product.code}: mevcut üretim/temin tanımı kuraldan farklı; değiştirilmedi.', product=product)
            continue
        from .product_rules import has_open_work
        if (not product.supply_method or (area and not product.production_area)) and has_open_work(product.pk):
            add_route_warning(result, f'{product.code}: açık üretim işi var; mevcut üretim/temin tanımı korundu.', product=product)
            continue
        route = ProductionRoute.objects.filter(product=product,rule_key=RULE_KEY).first()
        if route:
            if route.supply_method!=supply or (route.flow.code if route.flow_id else 'purchase')!=method:
                add_route_warning(result, f'{product.code}: mevcut rota tanımı korunarak incelemeye bırakıldı.', product=product)
                continue
            result['existing'] += 1
        else:
            definitions = []
            flow = None if method=='purchase' else flows.get(method)
            if method!='purchase':
                if not flow or flow.area!=method:
                    raise ValidationError(f'{method}: standart üretim akışı eksik veya alanı uyuşmuyor.')
                definitions = list(flow.steps.all())
                if not definitions:
                    raise ValidationError(f'{flow}: aşama tanımları eksik.')
            version = (ProductionRoute.objects.filter(product=product).aggregate(n=Max('version'))['n'] or 0)+1
            title = {'casting': 'Döküm rotası', 'wood': 'Ahşap rotası', 'purchase': 'Hazır temin rotası'}[method]
            route = ProductionRoute.objects.create(product=product,name=f'{product.code} · {title}',version=version,flow=flow,supply_method=supply,rule_key=RULE_KEY)
            if method=='purchase':
                RouteStep.objects.create(route=route,sequence=1,name='Dışarıdan hazır temin',workstation='Dış tedarik',duration_minutes=None)
            else:
                RouteStep.objects.bulk_create([RouteStep(route=route,sequence=s.sequence,name=s.name,workstation='Döküm' if method=='casting' else 'Ahşap',duration_minutes=s.duration_minutes) for s in definitions])
            result['created'] += 1
        updates = []
        if not product.supply_method:
            product.supply_method = supply
            updates.append('supply_method')
        if area and not product.production_area:
            product.production_area = area
            updates.append('production_area')
        if updates:
            product.save(update_fields=updates)
        counts[method] += 1
    raw_result = apply_raw_material_routes(source=source)
    result['created'] += raw_result['created']
    result['existing'] += raw_result['existing']
    result['skipped'].extend(raw_result['skipped'])
    result['warnings'].extend(raw_result['warnings'])
    counts.update(raw_result['assignments'])
    result['assignments'] = dict(counts)
    return result


@transaction.atomic
def apply_raw_material_routes(source=None, product_ids=None):
    """Procure raw cards without rewriting historical routes or open jobs."""
    from .product_rules import has_open_work
    products = Product.objects.filter(kind='raw')
    if product_ids is not None:
        products = products.filter(pk__in=product_ids)
    lines = MaterialRequirement.objects.all()
    if product_ids is not None:
        lines = lines.filter(component_id__in=product_ids)
    if source is not None:
        lines = lines.filter(source=source)
        referenced = set(lines.values_list('component_id', flat=True))
        referenced.update(MaterialRecipe.objects.filter(source=source).values_list('product_id', flat=True))
        products = products.filter(pk__in=referenced)
    result = {'created': 0, 'existing': 0, 'assignments': {}, 'skipped': [], 'warnings': []}
    for line in lines.filter(component__isnull=True):
        if normalized(line.component_kind) == 'hammadde' and not is_codeless_grill_material(line.component_kind, line.sheet, line.component_code):
            add_route_warning(result, f'{line.sheet}:{line.row_number}: hammadde kartı eşleştirilmemiş; temin rotası atanmadı.', line=line)
    assigned = 0
    for product in products.select_for_update().order_by('pk'):
        routes = list(product.productionroute_set.order_by('-version', '-pk'))
        incompatible = [r for r in routes if r.active and (r.supply_method != 'purchase' or r.flow_id)]
        needs_change = product.supply_method != 'purchase' or bool(product.production_area) or bool(incompatible)
        if needs_change and has_open_work(product.pk):
            add_route_warning(result, f'{product.code}: açık üretim işi var; mevcut tanım korundu. İş kapatıldıktan sonra temin rotasını tekrar uygulayın.', product=product)
            continue
        route = next((r for r in routes if r.active and r.supply_method == 'purchase' and not r.flow_id), None)
        if route is None:
            # An older managed definition may remain linked to completed jobs.
            # Keep it intact and use a fresh version rather than reusing its key.
            rule_key = '' if any(r.rule_key == RAW_RULE_KEY for r in routes) else RAW_RULE_KEY
            version = max((r.version for r in routes), default=0) + 1
            route = ProductionRoute.objects.create(product=product, name=f'{product.code} · Hammadde temin rotası',
                version=version, supply_method='purchase', flow=None, rule_key=rule_key)
            RouteStep.objects.create(route=route, sequence=1, name='Dışarıdan hazır temin',
                workstation='Dış tedarik', duration_minutes=None)
            result['created'] += 1
        else:
            result['existing'] += 1
        for old in incompatible:
            old.active = False
            old.save(update_fields=['active'])
        updates = []
        if product.supply_method != 'purchase':
            product.supply_method = 'purchase'
            updates.append('supply_method')
        if product.production_area:
            product.production_area = ''
            updates.append('production_area')
        if updates:
            product.save(update_fields=updates, sync_raw_route=False)
        assigned += 1
    if assigned:
        result['assignments']['purchase'] = assigned
    return result
