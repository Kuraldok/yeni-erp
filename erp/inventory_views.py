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
    actual_quantity = forms.DecimalField(label='Gerçekleşen üretim miktarı',max_digits=14,decimal_places=3,min_value=Decimal('0.001'))
    def __init__(self,*args,unit,**kwargs):
        self.unit=unit
        super().__init__(*args,**kwargs)
        self.fields['actual_quantity'].widget.attrs.update(step='1' if is_count_unit(unit) else '0.001',min='1' if is_count_unit(unit) else '0.001')
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
