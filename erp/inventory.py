from collections import defaultdict
from decimal import Decimal, DecimalException, localcontext

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max

from .audit import with_actor
from .models import (ConsumptionRecipe, ConsumptionRecipeLine, MaterialRecipe,
    Product, ProductionInputSnapshot, ProductionPosting, StockMovement, Warehouse, stock_balance)
from .quantities import is_fractional_count, validate_count


def validate_consumption_recipe(recipe):
    issues = []
    if not recipe.product_id or not recipe.product.unit or recipe.unit != recipe.product.unit:
        issues.append('Çıktı stok kartının birimi tanımlı olmalı ve reçeteyle uyuşmalıdır.')
    if recipe.product_id and recipe.product.kind in ('raw','part'):
        issues.append('Üretim çıktısı mamul veya yarı mamul olmalıdır.')
    lines = list(recipe.inputs.filter(active=True).select_related('product','source_line')) if recipe.pk else []
    if not lines:
        issues.append('Doğrudan tüketilecek en az bir girdi tanımlayın.')
    for line in lines:
        label = str(line.product) if line.product_id else f'Girdi {line.pk}'
        if not line.product_id:
            issues.append(f'{label}: stok kartı eşleştirmesi eksik.')
        elif line.product_id == recipe.product_id:
            issues.append(f'{label}: çıktı kendi girdisi olamaz.')
        if line.quantity is None or line.quantity <= 0:
            issues.append(f'{label}: pozitif tüketim miktarı girin.')
        if not line.unit or (line.product_id and line.unit != line.product.unit):
            issues.append(f'{label}: girdi birimi stok kartıyla uyuşmuyor veya boş.')
        try:
            validate_count(line.quantity,line.unit)
        except ValidationError as exc:
            issues.extend(f'{label}: {message}' for message in exc.messages)
        if line.source_line_id and line.product_id:
            kind = line.source_line.component_kind.casefold().strip()
            if (kind == 'hammadde' and line.product.kind != 'raw') or (kind in ('yarı mamul','mamul') and line.product.kind in ('raw','part')):
                issues.append(f'{label}: seçilen kart kaynak bileşenin türüyle uyuşmuyor.')
    if issues:
        raise ValidationError(issues)
    return lines


def prepare_production_inputs(job):
    if not job.input_warehouse_id or not job.output_warehouse_id:
        raise ValidationError('Girdi çıkış ve ürün giriş depolarını seçin.')
    if not job.product.unit or job.unit != job.product.unit:
        raise ValidationError('Üretilecek kartın birimini tanımlayın; iş emri birimiyle aynı olmalıdır.')
    if not job.consumption_recipe_id:
        raise ValidationError('Stok tüketim reçetesini seçin. Eksik girdi miktarları tahmin edilmez.')
    recipe = ConsumptionRecipe.objects.select_for_update().get(pk=job.consumption_recipe_id)
    if not recipe.active or not recipe.confirmed or recipe.product_id != job.product_id or recipe.unit != job.unit:
        raise ValidationError('Ürüne ait etkin, doğrulanmış ve birimi uygun tüketim reçetesi seçin.')
    lines = validate_consumption_recipe(recipe)
    if job.flow.area == 'casting':
        raw_names = {line.product.name for line in lines if line.product.kind == 'raw'}
        selected = 'Alüminyum Külçe' if job.casting_material == 'aluminium' else 'Pik'
        if raw_names and selected not in raw_names:
            raise ValidationError('Seçilen döküm hammaddesi tüketim reçetesinde bulunmuyor.')
    if job.stock_inputs.exists():
        raise ValidationError('Bu işin tüketim kopyası zaten oluşturulmuş.')
    ProductionInputSnapshot.objects.bulk_create([
        ProductionInputSnapshot(job=job,product=line.product,product_label=str(line.product)[:240],quantity=line.quantity,unit=line.unit)
        for line in lines
    ])


def exact_stock_quantity(value):
    with localcontext() as context:
        context.prec = 60
        rounded = value.quantize(Decimal('0.000000000001'))
        if rounded != value or rounded <= 0 or rounded >= Decimal('1e12'):
            raise ValidationError('Tüketim miktarı stok hassasiyeti veya miktar sınırına uymuyor; reçeteyi inceleyin.')
        return rounded


@with_actor
@transaction.atomic
def post_production(job, actor, actual_quantity):
    if not actor or not actor.has_perm('erp.post_production_stock'):
        raise PermissionDenied('Üretim stok işlemi yetkiniz yok.')
    if job.state != 'running' or ProductionPosting.objects.filter(job=job).exists():
        raise ValidationError('Bu üretimin stok işlemi tamamlanmış veya iş üretimde değil.')
    if job.steps.filter(completed_at__isnull=True).count() != 1:
        raise ValidationError('Stok işlemi yalnızca son üretim aşamasında yapılabilir.')
    from django.core.validators import DecimalValidator
    try:
        actual = Decimal(actual_quantity)
    except (DecimalException,ValueError,TypeError) as exc:
        raise ValidationError('Geçerli bir üretilen miktar girin.') from exc
    if not actual.is_finite() or actual <= 0:
        raise ValidationError('Pozitif üretilen miktar girin.')
    DecimalValidator(14,3)(actual)
    validate_count(actual,job.unit)
    warehouse_ids = {job.input_warehouse_id,job.output_warehouse_id}
    if None in warehouse_ids:
        raise ValidationError('Üretim depoları eksik.')
    list(Warehouse.objects.select_for_update().filter(pk__in=warehouse_ids).order_by('pk'))
    inputs = list(job.stock_inputs.all())
    if not inputs:
        raise ValidationError('Doğrulanmış tüketim kopyası yok; stok işlemi oluşturulmadı.')
    product_ids = {job.product_id} | {line.product_id for line in inputs}
    products = {product.pk:product for product in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')}
    if products[job.product_id].unit != job.unit:
        raise ValidationError('Üretim sırasında çıktı birimi değişmiş; stok işlemi durduruldu.')
    quantities = defaultdict(Decimal)
    with localcontext() as context:
        context.prec = 60
        for line in inputs:
            if products[line.product_id].unit != line.unit:
                raise ValidationError(f'{line.product_label}: stok kartının birimi tüketim kopyasından farklı.')
            quantities[line.product_id] += line.quantity * actual
    errors = []
    for product_id, quantity in quantities.items():
        quantity = exact_stock_quantity(quantity)
        validate_count(quantity,products[product_id].unit)
        quantities[product_id] = quantity
        if stock_balance(products[product_id],job.input_warehouse_id) < quantity:
            errors.append(f'{products[product_id]}: girdi deposunda yetersiz stok; gereken {quantity} {products[product_id].unit}.')
    if errors:
        raise ValidationError(errors)
    posting = ProductionPosting.objects.create(job=job,quantity=actual,unit=job.unit,
        recipe_label=str(job.consumption_recipe)[:240],input_warehouse_id=job.input_warehouse_id,
        output_warehouse_id=job.output_warehouse_id,created_by=actor)
    for product_id, quantity in sorted(quantities.items()):
        StockMovement.objects.create(product=products[product_id],warehouse_id=job.input_warehouse_id,
            kind='out',quantity=quantity,posting=posting,note=f'Üretim {job.number} · girdi tüketimi'[:240],created_by=actor)
    StockMovement.objects.create(product=products[job.product_id],warehouse_id=job.output_warehouse_id,
        kind='in',quantity=actual,posting=posting,note=f'Üretim {job.number} · üretim girişi'[:240],created_by=actor)
    job.actual_quantity = actual
    job.save(update_fields=['actual_quantity'])
    return posting


@with_actor
@transaction.atomic
def copy_consumption_recipe(source_id, actor):
    source = MaterialRecipe.objects.get(pk=source_id)
    if not source.product_id:
        raise ValidationError('Kaynak reçetenin çıktı stok kartı eşleştirilmemiş.')
    product = Product.objects.select_for_update().get(pk=source.product_id)
    if not product.unit:
        raise ValidationError('Önce çıktı kartının birimini tanımlayın.')
    version = (ConsumptionRecipe.objects.filter(product=product).aggregate(n=Max('version'))['n'] or 0)+1
    recipe = ConsumptionRecipe.objects.create(product=product,name=f'{product.code} · tüketim',version=version,unit=product.unit,source=source)
    units = {'adet':'adet','kg':'kg','metre':'m','litre':'lt','m':'m','lt':'lt'}
    for line in source.lines.all():
        unit = units.get(line.unit.strip().casefold(),'')
        quantity = None if is_fractional_count(line.quantity,unit) else line.quantity
        ConsumptionRecipeLine.objects.create(recipe=recipe,source_line=line,
            product_id=line.component_id if line.component_id != product.pk else None,
            quantity=quantity,unit=unit)
    return recipe
