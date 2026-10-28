from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum, Q

POSITIVE = MinValueValidator(Decimal('0.001'))

class Product(models.Model):
    code = models.CharField('Ürün kodu', max_length=100, unique=True)
    name = models.CharField('Ürün adı', max_length=200, blank=True)
    unit = models.CharField('Birim', max_length=12, choices=[('adet','Adet'),('kg','Kilogram'),('m','Metre'),('lt','Litre')], default='adet', blank=True)
    minimum_stock = models.DecimalField('Asgari stok', max_digits=14, decimal_places=3, default=0, null=True, blank=True, validators=[MinValueValidator(0)])
    kind = models.CharField('Kart türü', max_length=12, blank=True, choices=[('finished','Mamul'),('semi','Yarı mamul'),('raw','Hammadde'),('part','Parça')])
    production_area = models.CharField('Üretim alanı', max_length=10, blank=True, choices=[('wood','Ahşap'),('casting','Döküm')])
    supply_method = models.CharField('Temin yöntemi', max_length=12, blank=True, choices=[('manufacture','İç üretim'),('purchase','Dışarıdan hazır temin')])
    category = models.CharField('Ürün grubu', max_length=100, blank=True)
    active = models.BooleanField('Aktif', default=True)
    class Meta:
        ordering = ['code']
        verbose_name = 'Ürün'
        verbose_name_plural = 'Ürünler'
    def __str__(self): return f'{self.code} · {self.name}'

class Warehouse(models.Model):
    name = models.CharField('Depo adı', max_length=100, unique=True)
    class Meta:
        ordering = ['name']
        verbose_name = 'Depo'
        verbose_name_plural = 'Depolar'
    def __str__(self): return self.name

def stock_balance(product, warehouse=None):
    qs = StockMovement.objects.filter(product=product)
    if warehouse is not None: qs = qs.filter(warehouse=warehouse)
    return (qs.filter(kind='in').aggregate(n=Sum('quantity'))['n'] or Decimal(0)) - (qs.filter(kind='out').aggregate(n=Sum('quantity'))['n'] or Decimal(0))

class StockMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Ürün')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, verbose_name='Depo')
    kind = models.CharField('İşlem', max_length=3, choices=[('in','Giriş'),('out','Çıkış')])
    quantity = models.DecimalField('Miktar', max_digits=14, decimal_places=3, validators=[POSITIVE])
    note = models.CharField('Açıklama / belge no', max_length=240)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Kaydeden', editable=False)
    created_at = models.DateTimeField('Tarih', auto_now_add=True)
    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = 'Stok hareketi'
        verbose_name_plural = 'Stok hareketleri'
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='positive_stock_quantity')]
    def __str__(self): return f'{self.product.code} · {self.get_kind_display()} · {self.quantity}'
    def clean(self):
        if self.product_id and not self.product.unit:
            raise ValidationError({'product':'Ürün kartında ölçü birimi boş. Stok hareketinden önce birimi açıkça tanımlayın.'})
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Stok hareketi değiştirilemez. Düzeltme için ters hareket girin.')
        with transaction.atomic():
            # All movements in a warehouse use the same row lock on PostgreSQL.
            Warehouse.objects.select_for_update().get(pk=self.warehouse_id)
            self.full_clean()
            if self.kind == 'out' and stock_balance(self.product, self.warehouse) < self.quantity:
                raise ValidationError('Yetersiz stok. Çıkış miktarı mevcut stoktan büyük olamaz.')
            return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValidationError('Stok hareketi silinemez. Düzeltme için ters hareket girin.')

class Partner(models.Model):
    name = models.CharField('Unvan / ad', max_length=180)
    kind = models.CharField('Tür', max_length=10, choices=[('customer','Müşteri'),('supplier','Tedarikçi'),('both','Müşteri ve tedarikçi')])
    tax_number = models.CharField('Vergi / kimlik no', max_length=20, blank=True)
    email = models.EmailField('E-posta', blank=True)
    phone = models.CharField('Telefon', max_length=30, blank=True)
    address = models.TextField('Adres', blank=True)
    class Meta:
        ordering = ['name']
        verbose_name = 'Müşteri / tedarikçi'
        verbose_name_plural = 'Müşteri ve tedarikçiler'
    def __str__(self): return self.name

class Order(models.Model):
    number = models.CharField('Sipariş no', max_length=40, unique=True)
    kind = models.CharField('Tür', max_length=8, choices=[('sale','Satış'),('purchase','Satın alma')])
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, verbose_name='Müşteri / tedarikçi')
    status = models.CharField('Durum', max_length=12, choices=[('draft','Taslak'),('approved','Onaylandı'),('completed','Tamamlandı'),('cancelled','İptal edildi')], default='draft')
    date = models.DateField('Sipariş tarihi')
    due_date = models.DateField('Teslim tarihi', blank=True, null=True)
    currency = models.CharField('Para birimi', max_length=3, choices=[('TRY','Türk lirası'),('EUR','Euro'),('USD','ABD doları')], default='TRY')
    note = models.TextField('Not', blank=True)
    class Meta:
        ordering = ['-date', '-pk']
        verbose_name = 'Sipariş'
        verbose_name_plural = 'Siparişler'
    def __str__(self): return self.number
    def clean(self):
        if self.due_date and self.date and self.due_date < self.date:
            raise ValidationError({'due_date':'Teslim tarihi sipariş tarihinden önce olamaz.'})
        if self.partner_id and self.partner.kind not in (('customer','both') if self.kind == 'sale' else ('supplier','both')):
            raise ValidationError({'partner':'Sipariş türüne uygun müşteri veya tedarikçi seçin.'})
        if self.status in ('approved','completed') and (not self.pk or not self.lines.exists()):
            raise ValidationError({'status':'Önce taslak siparişi kaydedip en az bir kalem ekleyin.'})
    @property
    def total(self): return sum((line.total for line in self.lines.all()), Decimal(0))

class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='lines', verbose_name='Sipariş')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Ürün')
    quantity = models.DecimalField('Miktar', max_digits=14, decimal_places=3, validators=[POSITIVE])
    unit_price = models.DecimalField('Birim fiyat (KDV hariç)', max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    class Meta:
        verbose_name = 'Sipariş kalemi'
        verbose_name_plural = 'Sipariş kalemleri'
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='positive_order_quantity'), models.CheckConstraint(condition=Q(unit_price__gte=0), name='nonnegative_price')]
    def clean(self):
        previous = type(self).objects.filter(pk=self.pk).select_related('order').first() if self.pk else None
        if previous and previous.order.status != 'draft':
            raise ValidationError('Onaylı siparişin kalemi başka siparişe taşınamaz veya değiştirilemez.')
        if self.order_id and self.order.status != 'draft':
            raise ValidationError('Kalemler yalnızca taslak siparişlerde değiştirilebilir.')
    @property
    def total(self): return (self.quantity * self.unit_price).quantize(Decimal('0.01'))
    def __str__(self): return f'{self.order.number} · {self.product.code}'

class ProductionRoute(models.Model):
    name = models.CharField('Rota adı', max_length=160)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Üretilecek ürün')
    version = models.PositiveIntegerField('Sürüm', default=1, validators=[MinValueValidator(1)])
    active = models.BooleanField('Aktif', default=True)
    flow = models.ForeignKey('ProductionFlow', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Standart üretim akışı')
    supply_method = models.CharField('Temin yöntemi', max_length=12, blank=True, choices=[('manufacture','İç üretim'),('purchase','Dışarıdan hazır temin')])
    rule_key = models.CharField('Atama kuralı', max_length=40, blank=True, editable=False)
    class Meta:
        verbose_name = 'Üretim rotası'
        verbose_name_plural = 'Üretim rotaları'
        constraints = [models.UniqueConstraint(fields=['product','version'], name='unique_route_version'), models.UniqueConstraint(fields=['product','rule_key'], condition=~Q(rule_key=''), name='unique_managed_product_route')]
    def __str__(self): return f'{self.name} · v{self.version}'
    def clean(self):
        if self.supply_method=='purchase' and self.flow_id:
            raise ValidationError({'flow':'Hazır temin rotasında iç üretim akışı seçilemez.'})

class RouteStep(models.Model):
    route = models.ForeignKey(ProductionRoute, on_delete=models.PROTECT, related_name='steps', verbose_name='Rota')
    sequence = models.PositiveIntegerField('Sıra', validators=[MinValueValidator(1)])
    name = models.CharField('Operasyon', max_length=160)
    workstation = models.CharField('İş istasyonu', max_length=100)
    duration_minutes = models.DecimalField('Süre (dakika)', max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    instructions = models.TextField('Talimat', blank=True)
    class Meta:
        ordering = ['route','sequence']
        verbose_name = 'Rota adımı'
        verbose_name_plural = 'Rota adımları'
        constraints = [models.UniqueConstraint(fields=['route','sequence'], name='unique_route_sequence')]
    def __str__(self): return f'{self.route} · {self.sequence}. {self.name}'

class CatalogImport(models.Model):
    filename = models.CharField('Dosya', max_length=240)
    checksum = models.CharField('Dosya özeti', max_length=64, unique=True)
    created_at = models.DateTimeField('Aktarım tarihi', auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, editable=False)
    summary = models.JSONField('Aktarım özeti', default=dict)
    class Meta:
        ordering = ['-pk']
        verbose_name = 'Excel aktarımı'
        verbose_name_plural = 'Excel aktarımları'
    def __str__(self): return self.filename

class MaterialRecipe(models.Model):
    source = models.ForeignKey(CatalogImport, on_delete=models.PROTECT, verbose_name='Excel aktarımı')
    sheet = models.CharField('Excel sayfası', max_length=100)
    parent_code = models.CharField('Üst ürün kodu', max_length=100)
    parent_name = models.CharField('Üst ürün adı', max_length=200, blank=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Ürün kartı')
    issues = models.JSONField('İnceleme notları', default=list)
    class Meta:
        ordering = ['sheet','parent_code','pk']
        verbose_name = 'Ürün reçetesi'
        verbose_name_plural = 'Ürün reçeteleri'
        constraints = [models.UniqueConstraint(fields=['source','sheet','parent_code','parent_name'], name='unique_source_recipe')]
    def __str__(self): return f'{self.parent_code} · {self.parent_name} ({self.sheet})'

class MaterialRequirement(models.Model):
    source = models.ForeignKey(CatalogImport, on_delete=models.PROTECT, verbose_name='Excel aktarımı')
    recipe = models.ForeignKey(MaterialRecipe, on_delete=models.PROTECT, null=True, related_name='lines', verbose_name='Ürün reçetesi')
    sheet = models.CharField('Sayfa', max_length=100)
    row_number = models.PositiveIntegerField('Excel satırı')
    parent_code = models.CharField('Üst ürün kodu', max_length=100, blank=True)
    component_code = models.CharField('Bileşen kodu', max_length=100, blank=True)
    component_name = models.CharField('Bileşen adı', max_length=200, blank=True)
    component_kind = models.CharField('Bileşen türü', max_length=40, blank=True)
    quantity = models.DecimalField('Bir üst ürün için ihtiyaç', max_digits=24, decimal_places=12, null=True, blank=True, validators=[MinValueValidator(0)])
    unit = models.CharField('Ölçü birimi', max_length=30, blank=True)
    component = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Bileşen kartı')
    raw = models.JSONField('Excel değerleri', default=dict)
    formulas = models.JSONField('Kaynak formüller', default=dict)
    issues = models.JSONField('İnceleme notları', default=list)
    class Meta:
        ordering = ['sheet','row_number']
        verbose_name = 'Reçete satırı'
        verbose_name_plural = 'Reçete satırları'
        constraints = [models.UniqueConstraint(fields=['source','sheet','row_number'], name='unique_import_row')]
    def __str__(self): return f'{self.sheet}:{self.row_number} · {self.component_code or self.component_name}'

class ProductionFlow(models.Model):
    code = models.CharField('Akış kodu', max_length=30, unique=True)
    name = models.CharField('Akış adı', max_length=120)
    area = models.CharField('Alan', max_length=10, choices=[('wood','Ahşap'),('casting','Döküm'),('shared','Ortak bitirme')])
    class Meta:
        ordering = ['pk']
        verbose_name = 'Standart üretim akışı'
        verbose_name_plural = 'Standart üretim akışları'
    def __str__(self): return self.name

class ProductionFlowStep(models.Model):
    flow = models.ForeignKey(ProductionFlow, on_delete=models.PROTECT, related_name='steps', verbose_name='Akış')
    sequence = models.PositiveIntegerField('Sıra')
    name = models.CharField('Aşama', max_length=160)
    optional_assembly = models.BooleanField('İsteğe bağlı montaj', default=False)
    duration_minutes = models.DecimalField('Süre (dakika)', max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    class Meta:
        ordering = ['flow','sequence']
        constraints = [models.UniqueConstraint(fields=['flow','sequence'], name='unique_flow_step')]
        verbose_name = 'Standart akış adımı'
        verbose_name_plural = 'Standart akış adımları'
    def __str__(self): return f'{self.flow} · {self.sequence}. {self.name}'

class ProductionJob(models.Model):
    number = models.CharField('İş emri no', max_length=60, unique=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Üretilecek ürün / yarı mamul')
    quantity = models.DecimalField('Üretim miktarı', max_digits=14, decimal_places=3, validators=[POSITIVE])
    unit = models.CharField('Üretim birimi', max_length=12, choices=Product._meta.get_field('unit').choices)
    flow = models.ForeignKey(ProductionFlow, on_delete=models.PROTECT, verbose_name='Üretim akışı')
    route = models.ForeignKey(ProductionRoute, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Ürüne özel rota')
    recipe = models.ForeignKey(MaterialRecipe, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Malzeme reçetesi')
    casting_material = models.CharField('Döküm hammaddesi', max_length=10, blank=True, choices=[('aluminium','Alüminyum Külçe'),('pig','Pik')])
    assembly_required = models.BooleanField('Montaj gerekiyor mu?', null=True, blank=True)
    preceding_jobs = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='following_jobs', verbose_name='Önce tamamlanacak işler')
    state = models.CharField('Durum', max_length=12, default='draft', choices=[('draft','Taslak'),('running','Üretimde'),('completed','Tamamlandı')], editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    created_at = models.DateTimeField('Oluşturma tarihi', auto_now_add=True)
    note = models.TextField('Not', blank=True)
    class Meta:
        ordering = ['-pk']
        verbose_name = 'Üretim iş emri'
        verbose_name_plural = 'Üretim iş emirleri'
        permissions = [('advance_productionjob','Üretim aşamasını ilerletebilir')]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='positive_job_quantity')]
    def __str__(self): return self.number
    def clean(self):
        if self.route_id:
            if self.product_id and self.route.product_id!=self.product_id:
                raise ValidationError({'route':'Rota seçilen ürün / yarı mamul kartına ait olmalıdır.'})
            if not self.route.active:
                raise ValidationError({'route':'Pasif rota üretimde kullanılamaz.'})
            if self.route.supply_method=='purchase':
                raise ValidationError({'route':'Bu yarı mamul dışarıdan hazır temin edilir; iç üretim işi açılamaz.'})
            if self.flow_id and self.route.flow_id and self.route.flow_id!=self.flow_id:
                raise ValidationError({'flow':'Seçilen akış ürün rotasıyla uyuşmuyor.'})
        if self.product_id and self.product.supply_method=='purchase' and self.flow_id and self.flow.area!='shared':
            raise ValidationError({'product':'Dışarıdan hazır temin edilen kart için ahşap/döküm üretimi başlatılamaz.'})
        if self.product_id and self.product.unit and self.unit!=self.product.unit:
            raise ValidationError({'unit':'Üretim birimi ürün kartındaki birimle aynı olmalıdır.'})
        if self.recipe_id and self.product_id and self.recipe.product_id != self.product_id:
            raise ValidationError({'recipe':'Reçete seçilen ürün kartına bağlı olmalıdır.'})
        if self.flow_id:
            if self.flow.area=='casting' and not self.casting_material:
                raise ValidationError({'casting_material':'Alüminyum Külçe veya Pik seçin; seçim otomatik yapılmaz.'})
            if self.flow.area!='casting' and self.casting_material:
                raise ValidationError({'casting_material':'Bu seçim yalnızca döküm akışında kullanılır.'})
            if self.flow.area=='shared' and self.assembly_required is None:
                raise ValidationError({'assembly_required':'Montaj gerekip gerekmediğini açıkça seçin.'})
            if self.product_id and self.product.production_area and self.flow.area != 'shared' and self.product.production_area != self.flow.area:
                raise ValidationError({'flow':'Akış ürünün tanımlı üretim alanıyla uyuşmuyor.'})
        if self.recipe_id and self.casting_material:
            names = set(self.recipe.lines.filter(component_kind='Hammadde').values_list('component_name',flat=True))
            selected = 'Alüminyum Külçe' if self.casting_material=='aluminium' else 'Pik'
            if names and selected not in names:
                raise ValidationError({'casting_material':'Seçilen hammadde bu Excel reçetesinde bulunmuyor.'})

class ProductionJobStep(models.Model):
    job = models.ForeignKey(ProductionJob, on_delete=models.PROTECT, related_name='steps', verbose_name='İş emri')
    sequence = models.PositiveIntegerField('Sıra')
    name = models.CharField('Aşama', max_length=160)
    completed_at = models.DateTimeField('Tamamlanma tarihi', null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Tamamlayan')
    class Meta:
        ordering = ['sequence']
        constraints = [models.UniqueConstraint(fields=['job','sequence'], name='unique_job_step')]
        verbose_name = 'İş emri aşaması'
        verbose_name_plural = 'İş emri aşamaları'
    def __str__(self): return f'{self.job} · {self.sequence}. {self.name}'
