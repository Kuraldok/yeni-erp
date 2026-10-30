"""Explicit category rules for component production and procurement routes."""
from collections import defaultdict, Counter
import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from .models import MaterialRequirement, Product, ProductionFlow, ProductionRoute, RouteStep
from .workflows import ensure_workflows

# Keep the existing identity so repeated setup preserves routes and custom steps.
RULE_KEY = 'fountain-bench-v1'


def normalized(value):
    return ' '.join(unicodedata.normalize('NFC', value or '').casefold().replace('i\u0307', 'i').replace('ı', 'i').split())


CASTING_CATEGORIES = {normalized(name) for name in (
    'Şömine', 'Şömineler', 'Izgara', 'Izgaralar',
    'Posta kutusu', 'Posta kutuları', 'Soba', 'Sobalar',
    'Sandalye', 'Sandalyeler', 'Kapak', 'Kapaklar',
    'Bariyer', 'Bariyerler', 'Ferforje korkuluk', 'Ferforje korkuluklar',
    'Masa', 'Masalar', 'Masa ayağı', 'Masa ayakları',
    'Masa ve masa ayakları', 'Masalar ve masa ayakları',
    'Döküm saksı', 'Döküm saksılar', 'Döküm saksıları',
)}
WOOD_CATEGORIES = {normalized(name) for name in (
    'Ahşap saksı', 'Ahşap saksılar', 'Ahşap saksıları',
)}
MANGAL_CATEGORIES = {normalized(name) for name in ('Mangal', 'Mangallar')}
MANGAL_WOOD_COMPONENTS = {normalized(name) for name in (
    'Ahşap saplı kulp', 'Tel ızgara sapı',
)}


def accepts_finished_components(category):
    """Only the newly specified categories treat a Mamul component as semi-finished."""
    return normalized(category) in CASTING_CATEGORIES | WOOD_CATEGORIES | MANGAL_CATEGORIES


def is_semifinished_component(kind, category):
    kind = normalized(kind)
    return kind == 'yari mamul' or (kind == 'mamul' and accepts_finished_components(category))


def component_method(line):
    if not is_semifinished_component(line.component_kind, line.sheet):
        return None
    if line.component_code and line.component_code == line.parent_code:
        return None
    category = normalized(line.sheet)
    if category in CASTING_CATEGORIES:
        return 'casting'
    if category in WOOD_CATEGORIES:
        return 'wood'
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
    lines = list(MaterialRequirement.objects.select_related('component').all())
    affected = None
    if source is not None:
        affected = {line.component_id for line in lines if line.source_id==source.pk and component_method(line)}
    plans = defaultdict(set)
    finished_components = set()
    missing = []
    for line in lines:
        category = normalized(line.sheet)
        if is_semifinished_component(line.component_kind, line.sheet) and not normalized(line.component_name) and category in {'banklar', 'park bahçe banki'} | MANGAL_CATEGORIES:
            if source is None or line.source_id==source.pk:
                missing.append(f'{line.sheet}:{line.row_number}: bileşen adı boş; üretim/temin yolu atanmadı.')
            continue
        method = component_method(line)
        if not method or (affected is not None and line.component_id not in affected):
            continue
        if not line.component_id:
            missing.append(f'{line.sheet}:{line.row_number}: bileşen kartı eşleştirilmemiş.')
            continue
        plans[line.component_id].add(method)
        if accepts_finished_components(line.sheet):
            finished_components.add(line.component_id)
    if any(methods & {'wood', 'casting'} for methods in plans.values()):
        ensure_workflows()
    flows = {flow.code: flow for flow in ProductionFlow.objects.filter(code__in=['wood','casting'])}
    result = {'created': 0, 'existing': 0, 'assignments': {}, 'skipped': missing}
    counts = Counter()
    for product_id, methods in sorted(plans.items()):
        product = Product.objects.select_for_update().get(pk=product_id)
        if product.kind!='semi' and not (product.kind=='finished' and product_id in finished_components):
            result['skipped'].append(f'{product.code}: kartın türü yarı mamul değil; değiştirilmedi.')
            continue
        if len(methods)!=1:
            result['skipped'].append(f'{product.code}: kategoriler arasında farklı rota kuralları var; değiştirilmedi.')
            continue
        method = next(iter(methods))
        supply = 'purchase' if method=='purchase' else 'manufacture'
        area = '' if method=='purchase' else method
        if (product.supply_method and product.supply_method!=supply) or (product.production_area and product.production_area!=area):
            result['skipped'].append(f'{product.code}: mevcut üretim/temin tanımı kuraldan farklı; değiştirilmedi.')
            continue
        route = ProductionRoute.objects.filter(product=product,rule_key=RULE_KEY).first()
        if route:
            if route.supply_method!=supply or (route.flow.code if route.flow_id else 'purchase')!=method:
                result['skipped'].append(f'{product.code}: mevcut rota tanımı korunarak incelemeye bırakıldı.')
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
    result['assignments'] = dict(counts)
    return result
