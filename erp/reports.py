import csv
from collections import defaultdict
from decimal import Decimal,localcontext
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from .group_filters import available_groups,filter_by_group
from .models import (OrderLine,Product,ProductionJob,StockMovement,Warehouse)
from .stock import money,reserved_stock
from .quantities import is_count_unit,is_fractional_count


def quantity_cell(value,unit):
    if value is None:return None
    if is_fractional_count(value,unit):return f'{value.normalize()} (inceleme)'
    return int(value) if is_count_unit(unit) else value.normalize()


class ReportForm(forms.Form):
    kind=forms.ChoiceField(label='Rapor',choices=[('stock','Depo, rezervasyon ve stok değeri'),('production','Üretim ve maliyet'),('orders','Sipariş ve teslimat')])
    start=forms.DateField(label='Başlangıç',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='Bitiş',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    warehouse=forms.ModelChoiceField(label='Depo (stok raporu)',queryset=Warehouse.objects.all(),required=False)
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and data['start']>data['end']:raise forms.ValidationError('Başlangıç bitişten sonra olamaz.')
        return data


def require_report(user,kind):
    codes={'stock':['view_product','view_stockmovement','view_warehouse','view_stockreservation'],
        'production':['view_product','view_productionjob','view_productionposting','view_productioninputsnapshot','view_stockmovement'],
        'orders':['view_product','view_order','view_orderline']}[kind]
    if not all(user.has_perm('erp.'+code) for code in codes):raise PermissionDenied


def stock_rows(user,category,start,end,warehouse):
    products=filter_by_group(Product.objects.all(),category,user)
    movements=StockMovement.objects.filter(product__in=products).select_related('product','warehouse').order_by('pk')
    if warehouse:movements=movements.filter(warehouse=warehouse)
    if end:movements=movements.filter(created_at__date__lte=end)
    books={}
    with localcontext() as context:
        context.prec=60
        for move in movements:
            key=(move.product_id,move.warehouse_id)
            book=books.setdefault(key,{'product':move.product,'warehouse':move.warehouse,'opening':Decimal(0),'in':Decimal(0),'out':Decimal(0),'quantity':Decimal(0),'value':Decimal(0),'known':True,'adjustment':Decimal(0),'adjustment_known':True})
            sign=1 if move.kind=='in' else -1
            if start and timezone.localdate(move.created_at)<start:book['opening']+=sign*move.quantity
            else:book[move.kind]+=move.quantity
            book['quantity']+=sign*move.quantity
            if move.total_cost is None or move.value_adjustment is None:book['known']=False
            else:book['value']+=sign*(move.total_cost+move.value_adjustment)
            if not start or timezone.localdate(move.created_at)>=start:
                if move.value_adjustment is None:book['adjustment_known']=False
                else:book['adjustment']+=move.value_adjustment
            if book['quantity']==0:book['value']=Decimal(0);book['known']=True
        rows=[]
        historical=bool(end and end<timezone.localdate())
        for book in books.values():
            product=book['product'];reserved=None if historical else reserved_stock(product,book['warehouse'])
            quantities=[book['opening'],book['in'],book['out'],book['quantity'],reserved,None if reserved is None else book['quantity']-reserved]
            rows.append([product.code,product.name,book['warehouse'].name,product.get_unit_display(),*[quantity_cell(value,product.unit) for value in quantities],money(book['value']) if book['known'] else None,book['adjustment'] if book['adjustment_known'] else None])
    return ['Kod','Ürün','Depo','Birim','Açılış','Dönem giriş','Dönem çıkış','Kapanış','Ayrılan (güncel)','Serbest (güncel)','Stok değeri (TL)','Ters fiş değer farkı (dönem TL)'],rows


def production_totals(job,start=None,end=None):
    posts=job.stock_postings.exclude(reversal__status='posted')
    if start:posts=posts.filter(created_at__date__gte=start)
    if end:posts=posts.filter(created_at__date__lte=end)
    posts=list(posts)
    good=sum((p.quantity for p in posts),Decimal(0));scrap=sum((p.scrap_quantity for p in posts),Decimal(0))
    labor=sum((p.labor_cost for p in posts),Decimal(0));overhead=sum((p.overhead_cost for p in posts),Decimal(0))
    material=sum((p.material_cost for p in posts),Decimal(0)) if posts and all(p.material_cost is not None for p in posts) else None
    total=money(material+labor+overhead) if material is not None else None
    inputs=defaultdict(Decimal)
    for posting in posts:
        for move in posting.movements.filter(kind='out'):inputs[move.product_id]+=move.quantity
    rates=defaultdict(Decimal);labels={}
    for line in job.stock_inputs.select_related('product'):
        rates[line.product_id]+=line.quantity;labels[line.product_id]=(line.product.code,line.unit)
    variances=[f'{labels[pk][0]}: reçete {rate*(good+scrap)}, gerçek {inputs[pk]}, fark {inputs[pk]-rate*(good+scrap)} {labels[pk][1]}' for pk,rate in rates.items()]
    corrections=StockMovement.objects.filter(document_line__document__production_posting__job=job,document_line__document__status='posted')
    if start:corrections=corrections.filter(created_at__date__gte=start)
    if end:corrections=corrections.filter(created_at__date__lte=end)
    adjustments=list(corrections.values_list('value_adjustment',flat=True))
    adjustment=sum(adjustments,Decimal(0)) if all(value is not None for value in adjustments) else None
    return {'good':good,'scrap':scrap,'labor':labor,'overhead':overhead,'material':material,'total':total,'variance':' ; '.join(variances),'adjustment':adjustment,'has_correction':bool(adjustments)}


def production_rows(user,category,start,end):
    jobs=filter_by_group(ProductionJob.objects.select_related('product','order_line__order'),category,user)
    rows=[]
    for job in jobs:
        totals=production_totals(job,start,end)
        if (start or end) and not totals['good'] and not totals['scrap'] and not totals['has_correction']:
            created=timezone.localdate(job.created_at)
            if (start and created<start) or (end and created>end):continue
        rows.append([job.number,job.product.code,job.order_line.order.number if job.order_line_id else '',job.get_state_display(),job.unit,*[quantity_cell(value,job.unit) for value in (job.quantity,totals['good'],totals['scrap'],job.open_quantity)],totals['material'],totals['labor'],totals['overhead'],totals['total'],totals['adjustment'],totals['variance']])
    return ['İş','Ürün','Sipariş','Durum','Birim','Plan (toplam)','Sağlam (dönem)','Fire (dönem)','Kalan (toplam)','Malzeme TL','İşçilik TL','Diğer TL','Toplam TL','Ters fiş değer farkı TL','Reçete / gerçek tüketim'],rows


def order_rows(user,category,start,end):
    lines=filter_by_group(OrderLine.objects.select_related('order','product'),category,user)
    if start:lines=lines.filter(order__date__gte=start)
    if end:lines=lines.filter(order__date__lte=end)
    return ['Sipariş','Tür','Durum','Ürün','Birim','Sipariş miktarı','Teslim / kabul','Kalan','Kalem durumu'],[
        [line.order.number,line.order.get_kind_display(),line.order.get_status_display(),line.product_code,line.unit,*[quantity_cell(value,line.unit) for value in (line.quantity,line.fulfilled_quantity,line.remaining_quantity)],line.get_status_display()] for line in lines]


@login_required
def reports(request):
    kind=request.GET.get('kind','stock')
    if kind not in ('stock','production','orders'):kind='stock'
    require_report(request.user,kind)
    data=request.GET.copy();data['kind']=kind
    form=ReportForm(data)
    if not request.user.has_perm('erp.view_warehouse'):form.fields['warehouse'].queryset=Warehouse.objects.none()
    headers,rows=[],[]
    category=request.GET.get('category','').strip()
    if form.is_valid():
        start,end,warehouse=(form.cleaned_data.get(name) for name in ('start','end','warehouse'))
        if kind=='stock':headers,rows=stock_rows(request.user,category,start,end,warehouse)
        elif kind=='production':headers,rows=production_rows(request.user,category,start,end)
        else:headers,rows=order_rows(request.user,category,start,end)
    if request.GET.get('format')=='csv' and form.is_valid():
        response=HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="erp-rapor.csv"'
        response.write('\ufeff')
        writer=csv.writer(response,delimiter=';');writer.writerow(headers)
        def cell(value):
            if value is None:return 'Bilinmiyor'
            if isinstance(value,Decimal):return format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else format(value,'f')
            value=str(value)
            return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
        writer.writerows([[cell(value) for value in row] for row in rows]);return response
    params=request.GET.copy();params['format']='csv'
    from django.template.defaultfilters import floatformat
    displayed=[[floatformat(value,'2') if value is not None and 'TL' in headers[index] else value for index,value in enumerate(row)] for row in rows]
    return render(request,'erp/reports.html',{'form':form,'headers':headers,'rows':displayed,'category':category,'groups':available_groups(request.user),'can_filter_groups':True,'export_query':params.urlencode(),'kind':kind})
