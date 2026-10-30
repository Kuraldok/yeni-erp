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
    if recipe.product_id and not recipe.product.active:
        issues.append('Pasif çıktı kartıyla yeni üretim yapılamaz.')
    lines = list(recipe.inputs.filter(active=True).select_related('product','source_line')) if recipe.pk else []
    if not lines:
        issues.append('Doğrudan tüketilecek en az bir girdi tanımlayın.')
    for line in lines:
        label = str(line.product) if line.product_id else f'Girdi {line.pk}'
        if not line.product_id:
            issues.append(f'{label}: stok kartı eşleştirmesi eksik.')
        elif line.product_id == recipe.product_id:
            issues.append(f'{label}: çıktı kendi girdisi olamaz.')
        if line.product_id and not line.product.active:
            issues.append(f'{label}: pasif stok kartı üretimde kullanılamaz.')
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
        from .routing import normalized
        raw_names = {normalized(line.product.name) for line in lines if line.product.kind == 'raw'}
        selected = normalized('Alüminyum Külçe' if job.casting_material == 'aluminium' else 'Pik')
        alternative = normalized('Pik' if job.casting_material == 'aluminium' else 'Alüminyum Külçe')
        if alternative in raw_names:
            raise ValidationError('Seçilmeyen döküm hammaddesi tüketim reçetesinde etkin. Pik ve Alüminyum için ayrı sürüm kullanın veya diğer girdiyi tüketimden çıkarın.')
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
def post_production(job, actor, actual_quantity, *, scrap_quantity=0, scrap_reason='',
        labor_cost=0, overhead_cost=0, actual_inputs=None, expected_version=0,
        close_job=False, close_reason=''):
    from django.core.validators import DecimalValidator
    from .models import ProductionJob, StockReservation
    from .stock import (amount, available_stock, average_cost, consume_reservations,
        money, movement_cost, refresh_job_reservations, release_job_reservations)
    if not actor or not actor.has_perm('erp.post_production_stock'):
        raise PermissionDenied('Üretim stok işlemi yetkiniz yok.')
    job=ProductionJob.objects.select_for_update().select_related('product','order_line__order').get(pk=job.pk)
    if job.state!='running':raise ValidationError('Bu iş üretimde değil.')
    if job.posting_version!=expected_version:raise ValidationError('Üretim fişi sürümü değişti; sayfayı yenileyin. İşlem tekrarlanmadı.')
    if job.steps.filter(completed_at__isnull=True).count()!=1:raise ValidationError('Stok işlemi yalnızca son üretim aşamasında yapılabilir.')
    actual=amount(actual_quantity,job.unit,allow_zero=True).normalize()
    scrap=amount(scrap_quantity,job.unit,allow_zero=True).normalize()
    DecimalValidator(14,3)(actual);DecimalValidator(14,3)(scrap)
    if actual+scrap<=0:raise ValidationError('Üretilen veya fire miktarı pozitif olmalıdır.')
    if scrap and not str(scrap_reason or '').strip():raise ValidationError('Fire / hurda nedeni girin.')
    costs=[]
    for value in (labor_cost,overhead_cost):
        try:value=Decimal(str(value))
        except (DecimalException,ValueError,TypeError) as exc:raise ValidationError('Geçerli maliyet girin.') from exc
        if not value.is_finite() or value<0:raise ValidationError('Maliyet negatif olamaz.')
        DecimalValidator(18,2)(value);costs.append(value)
    if job.order_line_id and (job.order_line.status!='active' or job.order_line.order.status not in ('draft','approved')):
        raise ValidationError('Bağlı satış kalemi açık değil; üretim kaydedilmedi.')
    new_actual=(job.actual_quantity or Decimal(0))+actual
    DecimalValidator(14,3)(new_actual)
    if close_job and new_actual<job.quantity and not str(close_reason or '').strip():raise ValidationError('Eksik miktarla kapatma nedeni girin.')
    closed=new_actual>=job.quantity or close_job
    warehouse_ids={job.input_warehouse_id,job.output_warehouse_id}
    if None in warehouse_ids:raise ValidationError('Üretim depoları eksik.')
    list(Warehouse.objects.select_for_update().filter(pk__in=warehouse_ids).order_by('pk'))
    inputs=list(job.stock_inputs.all())
    if not inputs:raise ValidationError('Doğrulanmış tüketim kopyası yok.')
    product_ids={job.product_id}|{line.product_id for line in inputs}
    products={p.pk:p for p in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')}
    if not products[job.product_id].active or products[job.product_id].unit!=job.unit:raise ValidationError('Çıktı kartının etkinliği veya birimi değişmiş.')
    quantities=defaultdict(Decimal)
    with localcontext() as context:
        context.prec=60
        for line in inputs:
            if not products[line.product_id].active or products[line.product_id].unit!=line.unit:raise ValidationError(f'{line.product_label}: kart pasif veya birim farklı.')
            quantities[line.product_id]+=line.quantity*(actual+scrap)
    if actual_inputs is not None:
        if {int(pk) for pk in actual_inputs}!=set(quantities):raise ValidationError('Gerçek tüketimde tüm doğrudan girdiler bulunmalıdır.')
        quantities={int(pk):amount(value,products[int(pk)].unit) for pk,value in actual_inputs.items()}
    input_costs={};input_values={};errors=[]
    for pk,quantity in quantities.items():
        quantity=exact_stock_quantity(quantity);validate_count(quantity,products[pk].unit);quantities[pk]=quantity
        if available_stock(products[pk],job.input_warehouse_id,job_id=job.pk)<quantity:errors.append(f'{products[pk]}: girdi deposunda yetersiz serbest stok; gereken {quantity}.')
        input_costs[pk]=average_cost(products[pk],job.input_warehouse_id)
        input_values[pk]=movement_cost(products[pk],job.input_warehouse_id,quantity)
    if errors:raise ValidationError(errors)
    with localcontext() as context:
        context.prec=60
        material=money(sum(input_values.values(),Decimal(0))) if all(c is not None for c in input_values.values()) else None
        total=money(material+sum(costs)) if material is not None else None
        output_cost=money(total/actual) if total is not None and actual else None
    posting=ProductionPosting.objects.create(job=job,sequence=job.posting_version+1,quantity=actual,scrap_quantity=scrap,
        scrap_reason=scrap_reason,close_reason=close_reason if close_job else '',labor_cost=costs[0],overhead_cost=costs[1],material_cost=material,total_cost=total,
        unit=job.unit,recipe_label=str(job.consumption_recipe)[:240],input_warehouse_id=job.input_warehouse_id,output_warehouse_id=job.output_warehouse_id,created_by=actor)
    for pk,quantity in sorted(quantities.items()):
        StockMovement.objects.create(product=products[pk],warehouse_id=job.input_warehouse_id,kind='out',quantity=quantity,
            posting=posting,unit_cost=input_costs[pk],note=f'Üretim {job.number} · parti {posting.sequence} · tüketim'[:240],created_by=actor)
        consume_reservations(products[pk],job.input_warehouse_id,quantity,job_id=job.pk)
    if actual:
        StockMovement.objects.create(product=products[job.product_id],warehouse_id=job.output_warehouse_id,kind='in',quantity=actual,
            posting=posting,unit_cost=output_cost,total_cost=total,note=f'Üretim {job.number} · parti {posting.sequence} · ürün girişi'[:240],created_by=actor)
    job.actual_quantity=new_actual;job.posting_version+=1;job.save(update_fields=['actual_quantity','posting_version'])
    if closed:release_job_reservations(job,'Üretim kapatıldı; kullanılmayan miktar serbest.')
    else:refresh_job_reservations(job,actor)
    if job.order_line_id and actual:
        line=job.order_line
        # Sum across depots, keeping previously reserved finished goods intact.
        from django.db.models import Sum
        already=line.reservations.filter(state='active').aggregate(total=Sum('quantity'))['total'] or Decimal(0)
        reserve=min(actual,max(Decimal(0),line.remaining_quantity-already),max(Decimal(0),available_stock(job.product,job.output_warehouse_id)))
        if reserve:StockReservation.objects.create(product=job.product,warehouse_id=job.output_warehouse_id,order_line=line,quantity=reserve,unit=job.unit,created_by=actor)
    posting._closed=closed
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
