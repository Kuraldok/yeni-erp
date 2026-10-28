from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum, Q

POSITIVE = MinValueValidator(Decimal('0.001'))

class Product(models.Model):
    code = models.CharField('Ürün kodu', max_length=40, unique=True)
    name = models.CharField('Ürün adı', max_length=160)
    unit = models.CharField('Birim', max_length=12, choices=[('adet','Adet'),('kg','Kilogram'),('m','Metre'),('lt','Litre')], default='adet')
    minimum_stock = models.DecimalField('Asgari stok', max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
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
    class Meta:
        verbose_name = 'Üretim rotası'
        verbose_name_plural = 'Üretim rotaları'
        constraints = [models.UniqueConstraint(fields=['product','version'], name='unique_route_version')]
    def __str__(self): return f'{self.name} · v{self.version}'

class RouteStep(models.Model):
    route = models.ForeignKey(ProductionRoute, on_delete=models.PROTECT, related_name='steps', verbose_name='Rota')
    sequence = models.PositiveIntegerField('Sıra', validators=[MinValueValidator(1)])
    name = models.CharField('Operasyon', max_length=160)
    workstation = models.CharField('İş istasyonu', max_length=100)
    duration_minutes = models.DecimalField('Süre (dakika)', max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    instructions = models.TextField('Talimat', blank=True)
    class Meta:
        ordering = ['route','sequence']
        verbose_name = 'Rota adımı'
        verbose_name_plural = 'Rota adımları'
        constraints = [models.UniqueConstraint(fields=['route','sequence'], name='unique_route_sequence')]
    def __str__(self): return f'{self.route} · {self.sequence}. {self.name}'
