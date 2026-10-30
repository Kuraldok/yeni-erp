from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .audit import BusinessModel
from .models import Product
from .quantities import validate_count


class StockDocument(BusinessModel):
    number=models.CharField('Fiş no',max_length=60,unique=True)
    kind=models.CharField('Fiş türü',max_length=24,choices=[('delivery','Satış teslimatı'),('receipt','Satın alma mal kabul'),('transfer','Depolar arası transfer'),('count','Sayım farkı'),('reversal','Ters stok fişi'),('production_reversal','Üretim ters fişi')])
    order=models.ForeignKey('erp.Order',on_delete=models.PROTECT,null=True,blank=True,related_name='documents',verbose_name='Sipariş')
    source_warehouse=models.ForeignKey('erp.Warehouse',on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='Çıkış / sayım deposu')
    target_warehouse=models.ForeignKey('erp.Warehouse',on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='Giriş deposu')
    date=models.DateField('Belge tarihi',default=timezone.localdate)
    status=models.CharField('Durum',max_length=12,default='draft',choices=[('draft','Taslak'),('posted','İşlendi')],editable=False)
    reason=models.TextField('Açıklama / gerekçe',blank=True)
    reverses=models.OneToOneField('self',on_delete=models.PROTECT,null=True,blank=True,related_name='reversal_document',editable=False)
    production_posting=models.OneToOneField('erp.ProductionPosting',on_delete=models.PROTECT,null=True,blank=True,related_name='reversal',editable=False)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+',editable=False)
    created_at=models.DateTimeField(auto_now_add=True)
    posted_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,related_name='+',editable=False)
    posted_at=models.DateTimeField(null=True,editable=False)
    class Meta:
        ordering=['-pk']
        verbose_name='Stok belgesi'
        verbose_name_plural='Stok belgeleri'
        permissions=[('post_stockdocument','Stok belgesini işleyebilir'),('reverse_stockdocument','İşlenmiş stok / üretim fişini ters çevirebilir')]
    def __str__(self):return f'{self.number} · {self.get_kind_display()}'
    @property
    def is_reversed(self):return StockDocument.objects.filter(reverses=self,status='posted').exists()
    def save(self,*args,**kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk,status='posted').exists():
            raise ValidationError('İşlenmiş fiş değiştirilemez; ters fiş oluşturun.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):
        raise ValidationError('Stok belgesi silinemez.')


class StockDocumentLine(BusinessModel):
    document=models.ForeignKey(StockDocument,on_delete=models.PROTECT,related_name='lines')
    order_line=models.ForeignKey('erp.OrderLine',on_delete=models.PROTECT,null=True,blank=True,related_name='document_lines',verbose_name='Sipariş kalemi')
    product=models.ForeignKey(Product,on_delete=models.PROTECT,verbose_name='Stok kartı')
    quantity=models.DecimalField('Miktar / sayılan stok',max_digits=24,decimal_places=12,validators=[MinValueValidator(0)])
    unit=models.CharField('Birim',max_length=12,blank=True,editable=False)
    unit_cost=models.DecimalField('Birim maliyet (TL)',max_digits=32,decimal_places=8,null=True,blank=True,validators=[MinValueValidator(0)])
    baseline_quantity=models.DecimalField('Sayım öncesi stok',max_digits=24,decimal_places=12,null=True,editable=False)
    class Meta:
        ordering=['pk']
        verbose_name='Stok belgesi kalemi'
        verbose_name_plural='Stok belgesi kalemleri'
    def __str__(self):return f'{self.document.number} · {self.product}'
    def clean(self):
        validate_count(self.quantity,self.product.unit if self.product_id else self.unit)
        if self.document_id and self.document.status!='draft':raise ValidationError('İşlenmiş fişin kalemi değiştirilemez.')
    def save(self,*args,**kwargs):
        if self.document.status!='draft':raise ValidationError('İşlenmiş fişin kalemi değiştirilemez.')
        self.unit=self.product.unit
        if self._state.adding and self.document.kind=='count':
            from .models import stock_balance
            self.baseline_quantity=stock_balance(self.product,self.document.source_warehouse_id)
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):
        raise ValidationError('Fiş kalemleri silinmez; taslak fişi yeniden hazırlayın.')


class StockReservation(BusinessModel):
    product=models.ForeignKey(Product,on_delete=models.PROTECT,verbose_name='Stok kartı')
    warehouse=models.ForeignKey('erp.Warehouse',on_delete=models.PROTECT,verbose_name='Depo')
    job=models.ForeignKey('erp.ProductionJob',on_delete=models.PROTECT,null=True,blank=True,related_name='reservations',verbose_name='Üretim işi')
    order_line=models.ForeignKey('erp.OrderLine',on_delete=models.PROTECT,null=True,blank=True,related_name='reservations',verbose_name='Sipariş kalemi')
    quantity=models.DecimalField('Ayrılan miktar',max_digits=24,decimal_places=12,validators=[MinValueValidator(Decimal('0.000000000001'))])
    unit=models.CharField('Birim',max_length=12)
    state=models.CharField('Durum',max_length=12,default='active',choices=[('active','Etkin'),('released','Serbest bırakıldı'),('consumed','Kullanıldı')])
    reason=models.TextField('Açıklama / bırakma nedeni',blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['-pk']
        verbose_name='Stok rezervasyonu'
        verbose_name_plural='Stok rezervasyonları'
        permissions=[('release_stockreservation','Rezervasyonu serbest bırakabilir')]
        constraints=[models.CheckConstraint(condition=(Q(job__isnull=False,order_line__isnull=True)|Q(job__isnull=True,order_line__isnull=False)),name='one_reservation_owner'),models.CheckConstraint(condition=Q(quantity__gt=0),name='positive_reservation')]
    def __str__(self):return f'{self.product} · {self.quantity} {self.unit}'
