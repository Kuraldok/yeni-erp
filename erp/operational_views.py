from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import (Order, OrderLine, Product, ProductionJob, ProductionPosting,
    StockDocument, StockDocumentLine, StockMovement, StockReservation, Warehouse, stock_balance)
from .stock import (available_stock, document_number, post_document, release_reservation,
    reserve_order_line, reserved_stock, reverse_document, valuation)


def permissions(user,*codes):
    if not all(user.has_perm('erp.'+code) for code in codes):raise PermissionDenied


class ReasonForm(forms.Form):
    reason=forms.CharField(label='Gerekçe',max_length=2000,widget=forms.Textarea)


class DocumentForm(forms.ModelForm):
    class Meta:
        model=StockDocument
        fields=['number','kind','order','source_warehouse','target_warehouse','date','reason']
        widgets={'date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['kind'].choices=[(k,v) for k,v in self.fields['kind'].choices if k in ('delivery','receipt','transfer','count')]
        self.fields['order'].queryset=Order.objects.filter(status='approved')
        self.fields['number'].required=False
        self.fields['number'].help_text='Boş bırakırsanız fiş numarası oluşturulur.'
    def clean_number(self):return self.cleaned_data.get('number') or document_number()


class DocumentLineForm(forms.Form):
    order_line=forms.ModelChoiceField(label='Sipariş kalemi (teslimat / mal kabul)',queryset=OrderLine.objects.none(),required=False)
    product=forms.ModelChoiceField(label='Stok kartı (transfer / sayım)',queryset=Product.objects.none(),required=False)
    quantity=forms.DecimalField(label='Miktar / sayılan stok',max_digits=24,decimal_places=12,min_value=0)
    unit_cost=forms.DecimalField(label='Birim maliyet (TL, biliniyorsa)',max_digits=32,decimal_places=8,min_value=0,required=False)
    def __init__(self,*args,order=None,kind='',can_view_orders=False,**kwargs):
        self.kind=kind
        super().__init__(*args,**kwargs)
        self.fields['product'].queryset=Product.objects.filter(active=True).exclude(unit='')
        if order and can_view_orders:
            self.fields['order_line'].queryset=order.lines.filter(status='active').select_related('product')
            self.fields['order_line'].label_from_instance=lambda line:f'{line.product_code or line.product.code} · kalan {line.remaining_quantity} {line.unit}'
    def clean(self):
        data=super().clean()
        line=data.get('order_line');product=data.get('product')
        if self.kind in ('delivery','receipt'):
            if not line:raise forms.ValidationError('Sipariş kalemini seçin.')
            if product and product.pk!=line.product_id:raise forms.ValidationError('Sipariş kalemiyle stok kartı uyuşmuyor.')
            data['product']=line.product
        elif not product:raise forms.ValidationError('Stok kartını seçin.')
        elif line:raise forms.ValidationError('Transfer / sayımda sipariş kalemi seçilmez.')
        if data.get('product') and data.get('quantity') is not None:
            from .quantities import validate_count
            try:validate_count(data['quantity'],data['product'].unit)
            except ValidationError as exc:self.add_error('quantity',exc.messages)
        return data


DocumentLines=formset_factory(DocumentLineForm,extra=3,max_num=200,validate_max=True)


@login_required
def create_document(request):
    permissions(request.user,'add_stockdocument','view_stockdocument','view_stockdocumentline','view_product','view_warehouse')
    kind=request.POST.get('kind','') if request.method=='POST' else request.GET.get('kind','transfer')
    form=DocumentForm(request.POST or None,initial={'kind':kind,'order':request.GET.get('order'),'date':timezone.localdate()})
    can_orders=request.user.has_perm('erp.view_order') and request.user.has_perm('erp.view_orderline')
    if not can_orders:form.fields['order'].queryset=Order.objects.none()
    selected=request.POST.get('order','') if request.method=='POST' else request.GET.get('order','')
    from .orders import selection_pk
    order=Order.objects.filter(pk=selection_pk(selected)).first() if can_orders else None
    lines=DocumentLines(request.POST or None,prefix='items',form_kwargs={'order':order,'kind':kind,'can_view_orders':can_orders})
    if request.method=='POST' and 'refresh' not in request.POST:
        valid=form.is_valid();valid=lines.is_valid() and valid
        if valid:
            try:
                with transaction.atomic():
                    doc=form.save(commit=False);doc.created_by=request.user;doc.save()
                    records=[]
                    for item in lines:
                        if not item.cleaned_data:continue
                        row=item.cleaned_data
                        records.append(StockDocumentLine(document=doc,product=row['product'],order_line=row.get('order_line'),quantity=row['quantity'],unit=row['product'].unit,unit_cost=row.get('unit_cost')))
                    from .stock import validate_document
                    validate_document(doc,records)
                    for record in records:record.save()
            except ValidationError as exc:form.add_error(None,exc.messages)
            except (OperationalError,IntegrityError):form.add_error(None,'Fiş kaydedilemedi; numarayı ve güncel kayıtları kontrol edin.')
            else:return redirect('stock_document_detail',pk=doc.pk)
    return render(request,'erp/document_form.html',{'form':form,'lines':lines,'slug':'stok-belgeleri',
        'product_units':{str(pk):unit for pk,unit in Product.objects.filter(active=True).values_list('pk','unit')},
        'line_units':{str(line.pk):line.unit for line in order.lines.all()} if order else {}})


@login_required
def document_detail(request,pk):
    permissions(request.user,'view_stockdocument','view_stockdocumentline')
    document=get_object_or_404(StockDocument.objects.select_related('order','reverses','production_posting','created_by','posted_by'),pk=pk)
    reason_form=ReasonForm(request.POST or None)
    if request.method=='POST':
        try:
            if request.POST.get('action')=='post':post_document(pk,request.user)
            elif request.POST.get('action')=='reverse':
                if not reason_form.is_valid():raise ValidationError('Ters fiş gerekçesi girin.')
                reversal=reverse_document(pk,request.user,reason_form.cleaned_data['reason'])
                return redirect('stock_document_detail',pk=reversal.pk)
            else:raise ValidationError('Geçerli işlem seçin.')
        except ValidationError as exc:
            for message in exc.messages:messages.error(request,message)
        except (OperationalError,IntegrityError):messages.error(request,'Fiş işlenemedi; güncel fiş ve stok durumunu kontrol edin.')
        else:messages.success(request,'Fiş işlendi; stok hareketleri birlikte kaydedildi.')
        return redirect('stock_document_detail',pk=pk)
    moves=StockMovement.objects.filter(document_line__document=document).select_related('product','warehouse') if request.user.has_perm('erp.view_stockmovement') else []
    return render(request,'erp/document_detail.html',{'document':document,'lines':document.lines.select_related('product','order_line'),'movements':moves,'reason_form':reason_form,'slug':'stok-belgeleri'})


@login_required
def stock_card(request,pk):
    permissions(request.user,'view_product')
    product=get_object_or_404(Product,pk=pk)
    rows=[]
    if request.user.has_perm('erp.view_stockmovement') and request.user.has_perm('erp.view_warehouse'):
        for warehouse in Warehouse.objects.all():
            can_reserve=request.user.has_perm('erp.view_stockreservation')
            rows.append({'warehouse':warehouse,'quantity':stock_balance(product,warehouse),'reserved':reserved_stock(product,warehouse) if can_reserve else None,'available':available_stock(product,warehouse) if can_reserve else None,'valuation':valuation(product,warehouse)})
    return render(request,'erp/stock_card.html',{'product':product,'rows':rows,'slug':'stok-kartlari'})


@login_required
def order_tracking(request,pk):
    permissions(request.user,'view_order','view_orderline','view_product')
    order=get_object_or_404(Order,pk=pk)
    rows=[]
    for line in order.lines.select_related('product'):
        rows.append({'line':line,'fulfilled':line.fulfilled_quantity,'remaining':line.remaining_quantity,
            'jobs':line.production_jobs.all() if request.user.has_perm('erp.view_productionjob') else [],
            'reservations':line.reservations.filter(state='active') if request.user.has_perm('erp.view_stockreservation') else []})
    return render(request,'erp/order_tracking.html',{'order':order,'rows':rows,'documents':order.documents.all() if request.user.has_perm('erp.view_stockdocument') else [],'slug':'siparisler'})


class ReservationForm(forms.Form):
    warehouse=forms.ModelChoiceField(label='Depo',queryset=Warehouse.objects.all())
    quantity=forms.DecimalField(label='Ayrılacak miktar',max_digits=24,decimal_places=12,min_value=Decimal('0.000000000001'))


@login_required
def reserve_line(request,pk):
    permissions(request.user,'add_stockreservation','view_orderline','view_order','view_product','view_warehouse')
    line=get_object_or_404(OrderLine.objects.select_related('product','order'),pk=pk)
    form=ReservationForm(request.POST or None,initial={'quantity':line.remaining_quantity})
    from .quantities import is_count_unit,is_fractional_count
    if is_count_unit(line.unit):
        form.fields['quantity'].widget.attrs.update(step='1',min='1')
        if not is_fractional_count(line.remaining_quantity,line.unit):form.initial['quantity']=int(line.remaining_quantity)
    if request.method=='POST' and form.is_valid():
        try:reserve_order_line(pk,form.cleaned_data['warehouse'].pk,form.cleaned_data['quantity'],request.user)
        except ValidationError as exc:form.add_error(None,exc.messages)
        except (OperationalError,IntegrityError):form.add_error(None,'Rezervasyon kaydedilemedi; güncel durumu kontrol edin.')
        else:return redirect('order_tracking',pk=line.order_id)
    return render(request,'erp/action_form.html',{'title':f'{line} · stok ayır','form':form,'button':'Rezervasyon oluştur','slug':'rezervasyonlar'})


@login_required
def release_stock(request,pk):
    permissions(request.user,'view_stockreservation','release_stockreservation')
    reservation=get_object_or_404(StockReservation,pk=pk)
    form=ReasonForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:release_reservation(pk,request.user,form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc.messages)
        except (OperationalError,IntegrityError):form.add_error(None,'Rezervasyon güncellenemedi; durumu kontrol edin.')
        else:return redirect('listing',slug='rezervasyonlar')
    return render(request,'erp/action_form.html',{'title':f'{reservation} · serbest bırak','form':form,'button':'Serbest bırak','slug':'rezervasyonlar'})


@login_required
def cancel_production_job(request,pk):
    permissions(request.user,'view_productionjob','cancel_productionjob')
    job=get_object_or_404(ProductionJob,pk=pk)
    form=ReasonForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        from .production_ops import cancel_job
        try:cancel_job(pk,request.user,form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc.messages)
        except (OperationalError,IntegrityError):form.add_error(None,'İş iptal edilemedi; durumunu kontrol edin.')
        else:return redirect('production_job',pk=pk)
    return render(request,'erp/action_form.html',{'title':f'{job.number} · kalan üretimi iptal et','form':form,'button':'İşi iptal et','slug':'uretim-isleri','notice':'Kaydedilmiş partiler ve stok hareketleri korunur; kalan rezervasyonlar serbest bırakılır.'})


@login_required
def reverse_production_posting(request,pk):
    permissions(request.user,'view_productionposting','reverse_stockdocument')
    posting=get_object_or_404(ProductionPosting.objects.select_related('job'),pk=pk)
    form=ReasonForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        from .production_ops import reverse_production
        try:doc=reverse_production(pk,request.user,form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc.messages)
        except (OperationalError,IntegrityError):form.add_error(None,'Ters fiş oluşturulamadı; güncel durumu kontrol edin.')
        else:return redirect('stock_document_detail',pk=doc.pk)
    return render(request,'erp/action_form.html',{'title':f'{posting.job.number} · parti {posting.sequence} ters fişi','form':form,'button':'Ters üretim fişi oluştur','slug':'uretim-stok-fisleri','notice':'Özgün fiş korunur. Ürün çıkışı ve tüketilen girdilerin iadesi birlikte kaydedilir; ayrılmış veya yetersiz ürün stoğunda işlem durur.'})
