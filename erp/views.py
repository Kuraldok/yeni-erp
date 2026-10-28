from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError
from django.db.models import Q, CharField, TextField
from django.forms import modelform_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404
from .models import Product, Partner, Order, StockMovement, stock_balance
from .registry import MODULES

@login_required
def dashboard(request):
    stats = []
    for model, title in [(Product,'Ürün'),(Partner,'Müşteri / tedarikçi'),(Order,'Sipariş'),(StockMovement,'Stok hareketi')]:
        if request.user.has_perm(f'erp.view_{model._meta.model_name}'):
            stats.append((title, model.objects.count()))
    balances = []
    if request.user.has_perm('erp.view_stockmovement') and request.user.has_perm('erp.view_product'):
        for product in Product.objects.filter(active=True)[:50]:
            quantity = stock_balance(product)
            balances.append({'product':product, 'quantity':quantity, 'low':quantity < product.minimum_stock})
    recent = StockMovement.objects.select_related('product','warehouse','created_by')[:8] if request.user.has_perm('erp.view_stockmovement') else []
    return render(request, 'erp/dashboard.html', {'stats':stats,'balances':balances,'recent':recent})

def module(slug):
    if slug not in MODULES: raise Http404
    return MODULES[slug]

def permitted(user, action, model):
    return user.has_perm(f'erp.{action}_{model._meta.model_name}')

@login_required
def listing(request, slug):
    model, title, fields = module(slug)
    if not permitted(request.user,'view',model): raise PermissionDenied
    qs = model.objects.all()
    search = request.GET.get('q','').strip()
    if search:
        condition = Q()
        for field in model._meta.fields:
            if isinstance(field, (CharField, TextField)):
                condition |= Q(**{field.name+'__icontains':search})
        if condition: qs = qs.filter(condition)
    qs = qs.order_by(*model._meta.ordering) if model._meta.ordering else qs.order_by('-pk')
    if model is Order: qs = qs.prefetch_related('lines')
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    headers = []
    for field in fields:
        headers.append('Toplam (KDV hariç)' if field == 'total' else model._meta.get_field(field).verbose_name)
    rows = []
    for obj in page:
        values = []
        for field in fields:
            value = getattr(obj, f'get_{field}_display', lambda: getattr(obj, field))()
            if isinstance(value, bool): value = 'Evet' if value else 'Hayır'
            values.append(value)
        rows.append({'pk':obj.pk, 'values':values})
    return render(request,'erp/list.html', {'title':title,'slug':slug,'headers':headers,'rows':rows,'page':page,'q':search,'can_add':permitted(request.user,'add',model),'can_edit':model is not StockMovement and permitted(request.user,'change',model)})

@login_required
def edit(request, slug, pk=None):
    model, title, _ = module(slug)
    if model is StockMovement and pk is not None: raise PermissionDenied
    action = 'change' if pk is not None else 'add'
    if not permitted(request.user,action,model): raise PermissionDenied
    obj = get_object_or_404(model,pk=pk) if pk is not None else model()
    Form = modelform_factory(model, exclude=['created_by','created_at'], widgets={'date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'due_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')})
    form = Form(request.POST or None, instance=obj)
    # Do not leak names of related records through selectors without view permission.
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField) and not permitted(request.user,'view',field.queryset.model):
            field.queryset = field.queryset.none()
    if request.method == 'POST' and form.is_valid():
        record = form.save(commit=False)
        if model is StockMovement: record.created_by = request.user
        try:
            record.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        except OperationalError:
            form.add_error(None, 'Veri tabanı şu anda meşgul. Kaydın oluşup oluşmadığını kontrol edip tekrar deneyin.')
        else:
            messages.success(request, 'Kayıt başarıyla kaydedildi.')
            return redirect('listing',slug=slug)
    return render(request,'erp/form.html',{'form':form,'title':title,'slug':slug,'editing':pk is not None})
