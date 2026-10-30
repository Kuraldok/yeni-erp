from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import AuditEvent, ConsumptionRecipe, MaterialRecipe, ProductionPosting
from .quantities import is_count_unit, validate_count


class ProductionCompletionForm(forms.Form):
    actual_quantity = forms.DecimalField(label='Bu partide sağlam üretim',max_digits=14,decimal_places=3,min_value=0)
    scrap_quantity = forms.DecimalField(label='Bu partide fire / hurda',max_digits=14,decimal_places=3,min_value=0,required=False)
    scrap_reason = forms.CharField(label='Fire / hurda nedeni',required=False,widget=forms.Textarea)
    labor_cost = forms.DecimalField(label='Bu partinin işçiliği (TL)',max_digits=18,decimal_places=2,min_value=0,required=False)
    overhead_cost = forms.DecimalField(label='Bu partinin diğer üretim gideri (TL)',max_digits=18,decimal_places=2,min_value=0,required=False)
    close_job = forms.BooleanField(label='Plan eksik kalsa da bu partiyle işi kapat',required=False)
    close_reason = forms.CharField(label='Eksik üretimle kapatma nedeni',required=False,widget=forms.Textarea)
    expected_version = forms.IntegerField(widget=forms.HiddenInput,required=False)
    def __init__(self,*args,unit,job=None,**kwargs):
        self.unit=unit
        self.job=job
        self.input_units={}
        self.input_rates={}
        super().__init__(*args,**kwargs)
        for name in ('actual_quantity','scrap_quantity'):
            self.fields[name].widget.attrs.update(step='1' if is_count_unit(unit) else '0.001',min='0')
        if job:
            self.initial['expected_version']=job.posting_version
            for line in job.stock_inputs.select_related('product'):
                self.input_units[line.product_id]=line.unit
                self.input_rates[line.product_id]=self.input_rates.get(line.product_id,Decimal(0))+line.quantity
            for pk,unit in self.input_units.items():
                product=next(line.product for line in job.stock_inputs.select_related('product') if line.product_id==pk)
                self.fields[f'input_{pk}']=forms.DecimalField(label=f'Gerçek tüketim · {product} ({unit})',max_digits=24,decimal_places=12,min_value=Decimal('0.000000000001'),required=False,
                    help_text='Boşsa reçete × (sağlam üretim + fire) kullanılır.',widget=forms.NumberInput(attrs={'step':'1' if is_count_unit(unit) else '0.000000000001'}))
        value = self.initial.get('actual_quantity')
        if is_count_unit(unit) and value is not None and value == int(value):
            self.initial['actual_quantity'] = int(value)
    def clean_actual_quantity(self):
        quantity=self.cleaned_data['actual_quantity']
        try:
            validate_count(quantity,self.unit,'actual_quantity')
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return quantity
    def clean(self):
        data=super().clean()
        for name in ('scrap_quantity','labor_cost','overhead_cost'):
            if name not in self.errors:data[name]=data.get(name) or Decimal(0)
        if data.get('scrap_quantity'):
            try:validate_count(data['scrap_quantity'],self.unit,'scrap_quantity')
            except ValidationError as exc:self.add_error('scrap_quantity',exc.messages)
            if not data.get('scrap_reason'):self.add_error('scrap_reason','Fire / hurda nedeni girin.')
        if data.get('actual_quantity') is not None and data.get('actual_quantity',0)+(data.get('scrap_quantity') or 0)<=0:
            self.add_error(None,'Üretilen veya fire miktarı pozitif olmalıdır.')
        for pk,unit in self.input_units.items():
            value=data.get(f'input_{pk}')
            if value is not None:
                try:validate_count(value,unit,f'input_{pk}')
                except ValidationError as exc:self.add_error(f'input_{pk}',exc.messages)
        return data
    def batch_kwargs(self):
        data=self.cleaned_data
        result={name:data.get(name) for name in ('scrap_quantity','scrap_reason','labor_cost','overhead_cost','close_job','close_reason')}
        result['expected_version']=data.get('expected_version') or 0
        if any(data.get(f'input_{pk}') is not None for pk in self.input_units):
            gross=data['actual_quantity']+data['scrap_quantity']
            result['actual_inputs']={pk:data.get(f'input_{pk}') if data.get(f'input_{pk}') is not None else rate*gross for pk,rate in self.input_rates.items()}
        return result


@login_required
def consumption_recipe_detail(request,pk):
    if not request.user.has_perm('erp.view_consumptionrecipe') or not request.user.has_perm('erp.view_consumptionrecipeline'):
        raise PermissionDenied
    recipe=get_object_or_404(ConsumptionRecipe.objects.select_related('product','source','confirmed_by'),pk=pk)
    lines=recipe.inputs.select_related('product','source_line')
    issues=[]
    from .inventory import validate_consumption_recipe
    try:
        validate_consumption_recipe(recipe)
    except ValidationError as exc:
        issues=exc.messages
    return render(request,'erp/consumption_recipe.html',{'recipe':recipe,'lines':lines,'issues':issues,'slug':'tuketim-receteleri'})


@login_required
@require_POST
def copy_consumption(request,pk):
    if not all(request.user.has_perm('erp.'+code) for code in ('view_materialrecipe','view_materialrequirement','view_product','add_consumptionrecipe','add_consumptionrecipeline')):
        raise PermissionDenied
    get_object_or_404(MaterialRecipe,pk=pk)
    from .inventory import copy_consumption_recipe
    try:
        recipe=copy_consumption_recipe(pk,request.user)
    except ValidationError as exc:
        for message in exc.messages:messages.error(request,message)
    except (OperationalError,IntegrityError):
        messages.error(request,'Kopya kaydedilemedi. Tüketim reçetesi listesini kontrol edin.')
    else:
        messages.success(request,'Kaynak korunarak stok tüketim kopyası oluşturuldu. Girdileri eşleştirip doğrulayın.')
        return redirect('consumption_recipe_detail',pk=recipe.pk)
    return redirect('recipe_detail',pk=pk)


@login_required
def audit_detail(request,pk):
    if not request.user.has_perm('erp.view_auditevent'):
        raise PermissionDenied
    event=get_object_or_404(AuditEvent.objects.select_related('actor'),pk=pk)
    return render(request,'erp/audit_detail.html',{'event':event,'slug':'islem-gecmisi'})


@login_required
def production_posting_detail(request,pk):
    if not request.user.has_perm('erp.view_productionposting'):
        raise PermissionDenied
    posting=get_object_or_404(ProductionPosting.objects.select_related('job','created_by','input_warehouse','output_warehouse'),pk=pk)
    movements=posting.movements.select_related('product','warehouse','created_by')
    return render(request,'erp/production_posting.html',{'posting':posting,'movements':movements,'slug':'uretim-stok-fisleri'})
