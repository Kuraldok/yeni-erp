import csv
from decimal import Decimal
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from .group_filters import available_groups
from .models import Warehouse
from .report_data import quantity_cell, stock_rows, production_totals, production_rows, order_rows, stock_pairs, production_jobs, order_lines


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


@login_required
def reports(request):
    kind=request.GET.get('kind','stock')
    if kind not in ('stock','production','orders'):kind='stock'
    require_report(request.user,kind)
    data=request.GET.copy();data['kind']=kind
    form=ReportForm(data)
    if not request.user.has_perm('erp.view_warehouse'):form.fields['warehouse'].queryset=Warehouse.objects.none()
    headers,rows=[],[]
    page=None
    category=request.GET.get('category','').strip()
    if form.is_valid():
        start,end,warehouse=(form.cleaned_data.get(name) for name in ('start','end','warehouse'))
        exporting=request.GET.get('format')=='csv'
        if kind=='stock':
            if not exporting:page=Paginator(stock_pairs(request.user,category,end,warehouse),50).get_page(request.GET.get('page'))
            headers,rows=stock_rows(request.user,category,start,end,warehouse,pairs=None if exporting else list(page))
        elif kind=='production':
            if not exporting:page=Paginator(production_jobs(request.user,category,start,end),50).get_page(request.GET.get('page'))
            headers,rows=production_rows(request.user,category,start,end,jobs=None if exporting else page.object_list)
        else:
            if not exporting:page=Paginator(order_lines(request.user,category,start,end),50).get_page(request.GET.get('page'))
            headers,rows=order_rows(request.user,category,start,end,lines=None if exporting else page.object_list)
    if request.GET.get('format')=='csv' and form.is_valid():
        response=HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="erp-rapor.csv"'
        response.write('\ufeff')
        writer=csv.writer(response,delimiter=';');writer.writerow(headers)
        def cell(value):
            if value is None:return 'Bilinmiyor'
            if isinstance(value,int):return value
            if isinstance(value,Decimal):return format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else format(value,'f')
            value=str(value)
            return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
        writer.writerows([[cell(value) for value in row] for row in rows]);return response
    params=request.GET.copy();params.pop('page',None);params['format']='csv'
    page_params=request.GET.copy();page_params.pop('page',None);page_params.pop('format',None)
    from django.template.defaultfilters import floatformat
    displayed=[[floatformat(value,'2') if value is not None and 'TL' in headers[index] else value for index,value in enumerate(row)] for row in rows]
    return render(request,'erp/reports.html',{'form':form,'headers':headers,'rows':displayed,'category':category,'groups':available_groups(request.user),'can_filter_groups':True,'export_query':params.urlencode(),'kind':kind,'page':page,'page_query':page_params.urlencode()})
