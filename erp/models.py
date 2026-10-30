from decimal import Decimal, localcontext
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum, Q
from .audit import BusinessModel, AuditedQuerySet
from .quantities import is_fractional_count, validate_count

POSITIVE = MinValueValidator(Decimal('0.001'))

class Product(BusinessModel):
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
    def clean(self):
        validate_count(self.minimum_stock, self.unit, 'minimum_stock')
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.unit != self.unit and StockMovement.objects.filter(product=self).exists():
                raise ValidationError({'unit':'Stok geçmişi bulunan kartın birimi değiştirilemez.'})
            if previous and previous.unit != self.unit and any(is_fractional_count(quantity,self.unit) for quantity in OrderLine.objects.filter(product=self).values_list('quantity',flat=True)):
                raise ValidationError({'unit':'Kesirli miktarlı sipariş geçmişi bulunan kart Adet birimine çevrilemez; ayrı stok kartı açın.'})
            if previous and previous.unit and previous.unit != self.unit and OrderLine.objects.filter(product=self).exists():
                raise ValidationError({'unit':'Sipariş geçmişi bulunan kartın birimi değiştirilemez; ayrı stok kartı açın.'})
    def save(self,*args,**kwargs):
        previous=type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if previous and previous.unit and previous.unit!=self.unit and (StockMovement.objects.filter(product=self).exists() or OrderLine.objects.filter(product=self).exists()):
            raise ValidationError({'unit':'İşlem geçmişi bulunan kartın birimi değiştirilemez.'})
        return super().save(*args,**kwargs)

class Warehouse(BusinessModel):
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

class StockMovement(BusinessModel):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Ürün')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, verbose_name='Depo')
    kind = models.CharField('İşlem', max_length=3, choices=[('in','Giriş'),('out','Çıkış')])
    quantity = models.DecimalField('Miktar', max_digits=24, decimal_places=12, validators=[MinValueValidator(Decimal('0.000000000001'))])
    unit = models.CharField('İşlem birimi', max_length=12, blank=True, editable=False)
    posting = models.ForeignKey('ProductionPosting', on_delete=models.PROTECT, null=True, blank=True, editable=False, related_name='movements')
    document_line = models.ForeignKey('StockDocumentLine', on_delete=models.PROTECT, null=True, blank=True, editable=False, related_name='movements')
    reverses = models.OneToOneField('self',on_delete=models.PROTECT,null=True,blank=True,editable=False,related_name='reversal_movement')
    unit_cost = models.DecimalField('Birim maliyet (TL)',max_digits=32,decimal_places=8,null=True,blank=True,validators=[MinValueValidator(0)])
    total_cost = models.DecimalField('Hareket değeri (TL)',max_digits=40,decimal_places=8,null=True,blank=True,editable=False,validators=[MinValueValidator(0)])
    value_adjustment = models.DecimalField('Ters fiş değer farkı (TL)',max_digits=40,decimal_places=8,null=True,blank=True,default=0,editable=False)
    note = models.CharField('Açıklama / belge no', max_length=240)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Kaydeden', editable=False)
    created_at = models.DateTimeField('Tarih', auto_now_add=True)
    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = 'Stok hareketi'
        verbose_name_plural = 'Stok hareketleri'
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='positive_stock_quantity'), models.UniqueConstraint(fields=['posting','product','warehouse','kind'], condition=Q(posting__isnull=False), name='unique_production_stock_leg')]
    def __str__(self): return f'{self.product.code} · {self.get_kind_display()} · {self.quantity}'
    def clean(self):
        if self.product_id and not self.product.unit:
            raise ValidationError({'product':'Ürün kartında ölçü birimi boş. Stok hareketinden önce birimi açıkça tanımlayın.'})
        if self.product_id:
            validate_count(self.quantity, self.product.unit)
            if not self.product.active and not self.reverses_id:
                raise ValidationError({'product':'Pasif kartla yeni stok işlemi yapılamaz.'})
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Stok hareketi değiştirilemez. Düzeltme için ters hareket girin.')
        with transaction.atomic():
            # All movements in a warehouse use the same row lock on PostgreSQL.
            Warehouse.objects.select_for_update().get(pk=self.warehouse_id)
            self.product = Product.objects.select_for_update().get(pk=self.product_id)
            self.unit = self.product.unit
            self.full_clean()
            from .stock import available_stock, money, movement_cost
            if self.kind == 'out':
                owner_job = self.posting.job_id if self.posting_id else None
                owner_line = self.document_line.order_line_id if self.document_line_id and self.document_line.document.kind == 'delivery' else None
                if available_stock(self.product,self.warehouse,job_id=owner_job,order_line_id=owner_line) < self.quantity:
                    raise ValidationError('Yetersiz stok: serbest miktar çıkış için yeterli değil. Diğer sipariş ve üretim rezervasyonları kullanılamaz.')
                if not self.reverses_id:
                    self.total_cost=movement_cost(self.product,self.warehouse,self.quantity)
                    with localcontext() as context:
                        context.prec=60
                        self.unit_cost=money(self.total_cost/self.quantity) if self.total_cost is not None else None
                else:
                    current_value=movement_cost(self.product,self.warehouse,self.quantity)
                    with localcontext() as context:
                        context.prec=60
                        self.value_adjustment=money(current_value-self.total_cost) if current_value is not None and self.total_cost is not None else None
            elif self.total_cost is None and self.unit_cost is not None:
                with localcontext() as context:
                    context.prec=60
                    self.total_cost=money(self.quantity*self.unit_cost)
            for name in ('unit_cost','total_cost','value_adjustment'):
                self._meta.get_field(name).clean(getattr(self,name),self)
            return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValidationError('Stok hareketi silinemez. Düzeltme için ters hareket girin.')

class Partner(BusinessModel):
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

class Order(BusinessModel):
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
        if self.status in ('approved','completed') and (not self.pk or not self.lines.filter(status='active').exists()):
            raise ValidationError({'status':'Önce taslak siparişi kaydedip en az bir kalem ekleyin.'})
        if self.pk and self.kind in ('sale','purchase'):
            previous_kind = type(self).objects.filter(pk=self.pk).values_list('kind',flat=True).first()
            if previous_kind != self.kind or self.status in ('approved','completed'):
                from .orders import order_products
                if self.lines.filter(status='active').exclude(product__in=order_products(self.kind,include_inactive=True)).exists():
                    field = 'kind' if previous_kind != self.kind else 'status'
                    raise ValidationError({field:'Sipariş kalemleri bu türe uygun değil. Satışta yalnızca ana ürün, satın almada yalnızca hammadde kullanılabilir.'})
            previous = type(self).objects.get(pk=self.pk)
            if (previous.kind,previous.partner_id,previous.currency)!=(self.kind,self.partner_id,self.currency):
                if self.documents.filter(status='posted').exists() or self.lines.filter(production_jobs__state__in=['running','completed']).exists():
                    raise ValidationError('İşlenmiş fiş veya başlatılmış üretim bulunan siparişin türü, carisi ve para birimi değiştirilemez.')
            if self.status == 'completed' and previous.status != 'completed':
                if any(line.fulfilled_quantity < line.quantity for line in self.lines.filter(status='active')):
                    raise ValidationError({'status':'Teslimat / mal kabul miktarları tamamlanmadan sipariş tamamlanamaz.'})
            if self.status == 'cancelled' and previous.status != 'cancelled':
                if self.lines.filter(production_jobs__state='running').exists() or any(line.fulfilled_quantity for line in self.lines.all()):
                    raise ValidationError({'status':'Önce bağlı üretimleri durdurun ve teslimat / mal kabul fişlerini ters fişle düzeltin.'})
    @property
    def total(self): return sum((line.total for line in self.lines.all() if line.status == 'active'), Decimal(0))
    def save(self,*args,**kwargs):
        with transaction.atomic():
            previous=type(self).objects.filter(pk=self.pk).first() if self.pk else None
            if previous and previous.status!=self.status and self.status in ('completed','cancelled'):
                self.clean()
            result=super().save(*args,**kwargs)
            if previous and previous.status!=self.status and self.status=='cancelled':
                for reservation in StockReservation.objects.filter(order_line__order=self,state='active'):
                    reservation.state='released';reservation.reason='Sipariş iptal edildi.';reservation.save()
            return result

class OrderLine(BusinessModel):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='lines', verbose_name='Sipariş')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Ürün')
    quantity = models.DecimalField('Miktar', max_digits=14, decimal_places=3, validators=[POSITIVE])
    unit_price = models.DecimalField('Birim fiyat (KDV hariç)', max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    unit = models.CharField('İşlem birimi',max_length=12,blank=True,editable=False)
    product_code = models.CharField('Ürün kodu kopyası',max_length=100,blank=True,editable=False)
    product_name = models.CharField('Ürün adı kopyası',max_length=200,blank=True,editable=False)
    status = models.CharField('Kalem durumu', max_length=12, choices=[('active','Etkin'),('cancelled','İptal')], default='active', editable=False)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, editable=False, related_name='+', verbose_name='İptal eden')
    cancelled_at = models.DateTimeField('İptal tarihi', null=True, editable=False)
    cancellation_reason = models.TextField('İptal nedeni', blank=True, editable=False)
    class Meta:
        verbose_name = 'Sipariş kalemi'
        verbose_name_plural = 'Sipariş kalemleri'
        permissions = [('cancel_orderline','Sipariş kalemini iptal edebilir')]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='positive_order_quantity'), models.CheckConstraint(condition=Q(unit_price__gte=0), name='nonnegative_price')]
    def clean(self):
        previous = type(self).objects.filter(pk=self.pk).select_related('order').first() if self.pk else None
        if previous and previous.status == 'cancelled':
            raise ValidationError('İptal edilmiş kalem değiştirilemez.')
        if previous and previous.order.status != 'draft':
            raise ValidationError('Onaylı siparişin kalemi başka siparişe taşınamaz veya değiştirilemez.')
        if self.order_id and self.order.status != 'draft':
            raise ValidationError('Kalemler yalnızca taslak siparişlerde değiştirilebilir.')
        try:
            order = self.order
        except Order.DoesNotExist:
            order = None
        if order is not None and order.kind in ('sale','purchase') and self.product_id:
            from .orders import order_products
            if not order_products(order.kind,include_inactive=bool(previous and previous.product_id==self.product_id)).filter(pk=self.product_id).exists():
                raise ValidationError({'product':'Satış siparişinde yalnızca ana ürün, satın alma siparişinde yalnızca hammadde seçilebilir.'})
        if self.product_id:
            if not self.product.active and (not previous or previous.product_id != self.product_id or self.quantity != previous.quantity):
                raise ValidationError({'product':'Pasif ürün yeni siparişe veya miktar değişikliğine konu olamaz.'})
            validate_count(self.quantity,self.unit if previous and previous.product_id==self.product_id else self.product.unit)
        if previous and (previous.product_id != self.product_id or previous.quantity != self.quantity):
            if self.production_jobs.exclude(state='cancelled').exists() or self.document_lines.filter(document__status='posted').exists() or self.reservations.filter(state='active').exists():
                raise ValidationError('Bağlı üretim, fiş veya rezervasyon varken ürün ve miktar değiştirilemez.')
    def save(self,*args,**kwargs):
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if not self.product.active and (not previous or previous.product_id!=self.product_id or previous.quantity!=self.quantity):
            raise ValidationError({'product':'Pasif ürün yeni siparişe veya miktar değişikliğine konu olamaz.'})
        if not previous or previous.product_id != self.product_id:
            self.unit,self.product_code,self.product_name=self.product.unit,self.product.code,self.product.name
        return super().save(*args,**kwargs)
    @property
    def fulfilled_quantity(self):
        from .stock import fulfilled_quantity
        return fulfilled_quantity(self)
    @property
    def remaining_quantity(self):
        if self.status=='cancelled' or self.order.status in ('cancelled','completed'):return Decimal(0)
        return max(Decimal(0),self.quantity-self.fulfilled_quantity)
    def get_unit_display(self):
        return dict(Product._meta.get_field('unit').choices).get(self.unit,self.unit or 'Birim belirtilmedi')
    @property
    def total(self): return (self.quantity * self.unit_price).quantize(Decimal('0.01'))
    def __str__(self): return f'{self.order.number} · {self.product.code}'

class ProductionRoute(BusinessModel):
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

class RouteStep(BusinessModel):
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

class CatalogImport(BusinessModel):
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

class MaterialRecipe(BusinessModel):
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

class MaterialRequirement(BusinessModel):
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
    def clean(self):
        validate_count(self.quantity, self.unit)

class ProductionFlow(BusinessModel):
    code = models.CharField('Akış kodu', max_length=30, unique=True)
    name = models.CharField('Akış adı', max_length=120)
    area = models.CharField('Alan', max_length=10, choices=[('wood','Ahşap'),('casting','Döküm'),('shared','Ortak bitirme')])
    class Meta:
        ordering = ['pk']
        verbose_name = 'Standart üretim akışı'
        verbose_name_plural = 'Standart üretim akışları'
    def __str__(self): return self.name

class ProductionFlowStep(BusinessModel):
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

class ProductionJob(BusinessModel):
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
    state = models.CharField('Durum', max_length=12, default='draft', choices=[('draft','Taslak'),('running','Üretimde'),('completed','Tamamlandı'),('cancelled','İptal edildi')], editable=False)
    order_line = models.ForeignKey(OrderLine,on_delete=models.PROTECT,null=True,blank=True,related_name='production_jobs',verbose_name='Satış siparişi kalemi')
    posting_version = models.PositiveIntegerField(default=0,editable=False)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,editable=False,related_name='+')
    cancelled_at = models.DateTimeField(null=True,editable=False)
    cancellation_reason = models.TextField(blank=True,editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    created_at = models.DateTimeField('Oluşturma tarihi', auto_now_add=True)
    note = models.TextField('Not', blank=True)
    stock_mode = models.CharField('Stok bağlantısı', max_length=12, choices=[('inventory','Üretim ve stok'),('tracking','Yalnız aşama takibi')], default='inventory')
    consumption_recipe = models.ForeignKey('ConsumptionRecipe', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Stok tüketim reçetesi')
    input_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, null=True, blank=True, related_name='+', verbose_name='Girdi çıkış deposu')
    output_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, null=True, blank=True, related_name='+', verbose_name='Ürün giriş deposu')
    actual_quantity = models.DecimalField('Gerçekleşen üretim', max_digits=14, decimal_places=3, null=True, editable=False)
    class Meta:
        ordering = ['-pk']
        verbose_name = 'Üretim iş emri'
        verbose_name_plural = 'Üretim iş emirleri'
        permissions = [('advance_productionjob','Üretim aşamasını ilerletebilir'),('post_production_stock','Üretim stok işlemi yapabilir'),('track_production_without_stock','Stoksuz aşama takibi açabilir'),('cancel_productionjob','Üretim işini gerekçeyle iptal edebilir'),('link_production_orders','Üretim için açık satış kalemlerini seçebilir')]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='positive_job_quantity')]
    def __str__(self): return self.number
    @property
    def stock_posting(self):
        return self.stock_postings.order_by('pk').last()
    @property
    def remaining_quantity(self):
        return max(Decimal(0),self.quantity-(self.actual_quantity or Decimal(0)))
    @property
    def open_quantity(self):
        return self.remaining_quantity if self.state in ('draft','running') else Decimal(0)
    def clean(self):
        validate_count(self.quantity, self.unit)
        validate_count(self.actual_quantity, self.unit, 'actual_quantity')
        if self.product_id and not self.product.active:
            raise ValidationError({'product':'Pasif ürünle yeni üretim yapılamaz.'})
        if self.order_line_id:
            line=self.order_line
            if line.status!='active' or line.order.kind!='sale' or line.order.status not in ('draft','approved') or line.product_id!=self.product_id:
                raise ValidationError({'order_line':'Etkin ve ürünü aynı olan açık satış kalemi seçin.'})
        if self.consumption_recipe_id and self.product_id and self.consumption_recipe.product_id != self.product_id:
            raise ValidationError({'consumption_recipe':'Tüketim reçetesi seçilen ürüne ait olmalıdır.'})
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
        if self.recipe_id and self.casting_material and self.stock_mode == 'tracking':
            names = set(self.recipe.lines.filter(component_kind='Hammadde').values_list('component_name',flat=True))
            selected = 'Alüminyum Külçe' if self.casting_material=='aluminium' else 'Pik'
            if names and selected not in names:
                raise ValidationError({'casting_material':'Seçilen hammadde bu Excel reçetesinde bulunmuyor.'})

class ProductionJobStep(BusinessModel):
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


class ImmutableQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        if kwargs.get('update_conflicts') or kwargs.get('ignore_conflicts'):
            raise ValidationError('Geçmiş kayıtları toplu işlemle değiştirilemez veya sessizce geçilemez.')
        return super().bulk_create(objs, **kwargs)
    def update(self, **kwargs):
        raise ValidationError('Geçmiş kayıtları değiştirilemez.')
    def delete(self):
        raise ValidationError('Geçmiş kayıtları silinemez.')
    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Geçmiş kayıtları değiştirilemez.')


class FrozenBusinessQuerySet(AuditedQuerySet):
    update = ImmutableQuerySet.update
    delete = ImmutableQuerySet.delete
    bulk_update = ImmutableQuerySet.bulk_update


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, editable=False, verbose_name='Kullanıcı')
    actor_label = models.CharField('Kullanıcı / kaynak', max_length=160)
    action = models.CharField('İşlem', max_length=16, choices=[('create','Ekleme'),('update','Düzenleme'),('delete','Silme'),('cancel','İptal')])
    model_label = models.CharField('Kayıt türü', max_length=100)
    object_id = models.CharField('Kayıt kimliği', max_length=100)
    object_label = models.CharField('Kayıt', max_length=240)
    created_at = models.DateTimeField('Tarih / saat', auto_now_add=True)
    changes = models.JSONField('Değişiklikler', default=dict)
    reason = models.TextField('Neden', blank=True)
    objects = ImmutableQuerySet.as_manager()
    class Meta:
        ordering = ['-pk']
        verbose_name = 'İşlem geçmişi'
        verbose_name_plural = 'İşlem geçmişi'
        default_permissions = ('view',)
        indexes = [models.Index(fields=['model_label','object_id'])]
    def __str__(self): return f'{self.actor_label} · {self.get_action_display()} · {self.object_label}'
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('İşlem geçmişi değiştirilemez.')
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValidationError('İşlem geçmişi silinemez.')


class QuantityReview(BusinessModel):
    model_label = models.CharField('Kayıt türü', max_length=100)
    object_id = models.CharField('Kayıt kimliği', max_length=100)
    field_name = models.CharField('Alan', max_length=100)
    quantity = models.DecimalField('Kaynak miktar', max_digits=24, decimal_places=12)
    note = models.CharField('İnceleme', max_length=240, default='Adet miktarı kesirli; özgün değer korundu.')
    detected_at = models.DateTimeField('Tespit tarihi', auto_now_add=True)
    resolved_at = models.DateTimeField('Düzeltme tarihi', null=True, blank=True)
    class Meta:
        ordering = ['-pk']
        verbose_name = 'Miktar incelemesi'
        verbose_name_plural = 'Miktar incelemeleri'
        default_permissions = ('view',)
        constraints = [models.UniqueConstraint(fields=['model_label','object_id','field_name'],name='unique_quantity_review')]
    def __str__(self): return f'{self.model_label}:{self.object_id} · {self.quantity}'


class ConsumptionRecipe(BusinessModel):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Üretilecek ürün / yarı mamul')
    name = models.CharField('Reçete adı', max_length=160)
    version = models.PositiveIntegerField('Sürüm', default=1, validators=[MinValueValidator(1)])
    unit = models.CharField('Bir çıktı için birim', max_length=12, choices=Product._meta.get_field('unit').choices)
    source = models.ForeignKey(MaterialRecipe, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Kaynak Excel reçetesi')
    active = models.BooleanField('Aktif', default=True)
    confirmed = models.BooleanField('Girdiler bu ürünün doğrudan tüketimidir', default=False,
        help_text='Yarı mamulde tüketilmiş hammaddeleri mamul reçetesine tekrar eklemeyin. Girdi düzenlemesinden sonra yeniden doğrulayın.')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, editable=False, related_name='+', verbose_name='Doğrulayan')
    confirmed_at = models.DateTimeField('Doğrulama tarihi', null=True, editable=False)
    class Meta:
        ordering = ['product','version']
        verbose_name = 'Stok tüketim reçetesi'
        verbose_name_plural = 'Stok tüketim reçeteleri'
        constraints = [models.UniqueConstraint(fields=['product','version'],name='unique_consumption_version')]
    def __str__(self): return f'{self.name} · v{self.version}'
    @property
    def locked(self):
        return bool(self.pk and self.productionjob_set.exclude(state='draft').exists())
    def clean(self):
        if self.locked:
            raise ValidationError('Üretimde kullanılmış tüketim reçetesi korunur. Yeni sürüm oluşturun.')
        if self.product_id and (self.product.kind in ('raw','part') or not self.product.unit or self.unit != self.product.unit):
            raise ValidationError({'product':'Çıktı, birimi tanımlı bir mamul / yarı mamul olmalı; reçete birimi kartla uyuşmalıdır.'})
        if self.source_id and self.product_id and self.source.product_id != self.product_id:
            raise ValidationError({'source':'Kaynak reçete çıktı ürününe bağlı olmalıdır.'})
        if self.confirmed:
            from .inventory import validate_consumption_recipe
            validate_consumption_recipe(self)
    def save(self, *args, **kwargs):
        from django.utils import timezone
        from .audit import current_actor
        if self.locked:
            raise ValidationError('Üretimde kullanılmış reçete değiştirilemez.')
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if self.confirmed:
            from .inventory import validate_consumption_recipe
            validate_consumption_recipe(self)
            if not previous or not previous.confirmed:
                self.confirmed_at = timezone.now()
                self.confirmed_by = current_actor()
        else:
            self.confirmed_at = None
            self.confirmed_by = None
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'confirmed_at','confirmed_by'}
        return super().save(*args, **kwargs)


class ConsumptionRecipeLine(BusinessModel):
    recipe = models.ForeignKey(ConsumptionRecipe, on_delete=models.PROTECT, related_name='inputs', verbose_name='Tüketim reçetesi')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Girdi stok kartı')
    quantity = models.DecimalField('Bir çıktı için tüketim', max_digits=24, decimal_places=12, null=True, blank=True, validators=[MinValueValidator(Decimal('0.000000000001'))])
    unit = models.CharField('Girdi birimi', max_length=12, choices=Product._meta.get_field('unit').choices, blank=True)
    source_line = models.ForeignKey(MaterialRequirement, on_delete=models.PROTECT, null=True, blank=True, editable=False)
    active = models.BooleanField('Bu üretimde tüketilir', default=True)
    class Meta:
        ordering = ['pk']
        verbose_name = 'Tüketim girdisi'
        verbose_name_plural = 'Tüketim girdileri'
        constraints = [models.UniqueConstraint(fields=['recipe','source_line'],condition=Q(source_line__isnull=False),name='unique_consumption_source_row')]
    def __str__(self): return f'{self.recipe} · {self.product or "Eşleştirme bekliyor"}'
    def clean(self):
        validate_count(self.quantity, self.unit)
        if self.recipe_id and self.recipe.locked:
            raise ValidationError('Kullanılmış reçetenin girdileri değiştirilemez.')
        if self.recipe_id and self.product_id == self.recipe.product_id:
            raise ValidationError({'product':'Ürün kendi girdisi olamaz.'})
        if self.product_id and self.unit and self.unit != self.product.unit:
            raise ValidationError({'unit':'Girdi birimi stok kartının birimiyle aynı olmalıdır.'})
        if not self.product_id and not self.source_line_id:
            raise ValidationError({'product':'Girdi stok kartını seçin.'})
    def save(self, *args, **kwargs):
        with transaction.atomic():
            recipe = ConsumptionRecipe.objects.select_for_update().get(pk=self.recipe_id)
            if recipe.locked:
                raise ValidationError('Kullanılmış reçetenin girdileri değiştirilemez.')
            previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
            if previous and previous.recipe_id != self.recipe_id:
                raise ValidationError('Girdi başka reçeteye taşınamaz; yeni girdi ekleyin.')
            result = super().save(*args, **kwargs)
            if recipe.confirmed:
                recipe.confirmed = False
                recipe.save(update_fields=['confirmed','confirmed_at','confirmed_by'])
            return result


class ProductionInputSnapshot(BusinessModel):
    job = models.ForeignKey(ProductionJob, on_delete=models.PROTECT, related_name='stock_inputs')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_label = models.CharField('Girdi', max_length=240)
    quantity = models.DecimalField('Bir çıktı için tüketim', max_digits=24, decimal_places=12)
    unit = models.CharField('Birim', max_length=12)
    objects = FrozenBusinessQuerySet.as_manager()
    class Meta:
        ordering = ['pk']
        default_permissions = ('view',)
        verbose_name = 'Üretim tüketim kopyası'
        verbose_name_plural = 'Üretim tüketim kopyaları'
    def __str__(self): return f'{self.job.number} · {self.product_label}'
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Başlatılan üretimin tüketim kopyası değiştirilemez.')
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValidationError('Üretim tüketim kopyası silinemez.')


class ProductionPosting(BusinessModel):
    job = models.ForeignKey(ProductionJob, on_delete=models.PROTECT, related_name='stock_postings', verbose_name='İş emri')
    sequence = models.PositiveIntegerField(default=1)
    quantity = models.DecimalField('Üretilen miktar', max_digits=14, decimal_places=3)
    scrap_quantity = models.DecimalField('Fire / hurda miktarı',max_digits=14,decimal_places=3,default=0,validators=[MinValueValidator(0)])
    scrap_reason = models.TextField('Fire / hurda nedeni',blank=True)
    close_reason = models.TextField('Eksik miktarla kapatma nedeni',blank=True)
    labor_cost = models.DecimalField('İşçilik (TL)',max_digits=18,decimal_places=2,default=0,validators=[MinValueValidator(0)])
    overhead_cost = models.DecimalField('Diğer üretim gideri (TL)',max_digits=18,decimal_places=2,default=0,validators=[MinValueValidator(0)])
    material_cost = models.DecimalField('Malzeme maliyeti (TL)',max_digits=40,decimal_places=8,null=True,editable=False)
    total_cost = models.DecimalField('Toplam maliyet (TL)',max_digits=40,decimal_places=8,null=True,editable=False)
    unit = models.CharField('Birim', max_length=12)
    recipe_label = models.CharField('Tüketim reçetesi', max_length=240)
    input_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='+', verbose_name='Girdi deposu')
    output_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='+', verbose_name='Ürün deposu')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Kaydeden')
    created_at = models.DateTimeField('Tarih', auto_now_add=True)
    objects = FrozenBusinessQuerySet.as_manager()
    class Meta:
        ordering = ['-pk']
        default_permissions = ('view',)
        verbose_name = 'Üretim stok fişi'
        verbose_name_plural = 'Üretim stok fişleri'
        constraints = [models.UniqueConstraint(fields=['job','sequence'],name='unique_production_batch')]
    def __str__(self): return f'{self.job.number} · {self.quantity} {self.unit}'
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Üretim stok fişi değiştirilemez.')
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValidationError('Üretim stok fişi silinemez.')


from .stock_models import StockDocument, StockDocumentLine, StockReservation
