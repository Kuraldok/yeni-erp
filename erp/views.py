from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, IntegrityError, transaction
from django.db.models import Q, CharField, TextField
from django.forms import modelform_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404
from django.urls import reverse
from .models import Product, Partner, Order, StockMovement, ProductionJob, MaterialRecipe, stock_balance
from .registry import MODULES, READ_ONLY_MODELS
from .products import main_products, product_groups, filter_group

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
            balances.append({'product':product, 'quantity':quantity, 'low':product.minimum_stock is not None and quantity < product.minimum_stock})
    recent = StockMovement.objects.select_related('product','warehouse','created_by')[:8] if request.user.has_perm('erp.view_stockmovement') else []
    from .models import ProductionFlow, CatalogImport
    flows=ProductionFlow.objects.prefetch_related('steps').all() if request.user.has_perm('erp.view_productionflow') else []
    catalog=CatalogImport.objects.first() if request.user.has_perm('erp.view_catalogimport') else None
    return render(request, 'erp/dashboard.html', {'stats':stats,'balances':balances,'recent':recent,'flows':flows,'catalog':catalog})

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
    groups = []
    category = ''
    if model is Product:
        qs = main_products()
        groups = product_groups(qs)
        category = request.GET.get('category', '').strip()
        qs = filter_group(qs, category).prefetch_related('materialrecipe_set')
    search = request.GET.get('q','').strip()
    if search:
        condition = Q()
        for field in model._meta.fields:
            if isinstance(field, (CharField, TextField)):
                condition |= Q(**{field.name+'__icontains':search})
        if condition: qs = qs.filter(condition)
    qs = qs.order_by(*model._meta.ordering) if model._meta.ordering else qs.order_by('-pk')
    if model is Order: qs = qs.prefetch_related('lines')
    if model is MaterialRecipe: qs = qs.select_related('source','product')
    qs = qs.select_related()
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    headers = []
    for field in fields:
        headers.append('Toplam (KDV hariç)' if field == 'total' else model._meta.get_field(field).verbose_name)
    rows = []
    for obj in page:
        values = []
        for field in fields:
            value = getattr(obj, f'get_{field}_display', lambda: getattr(obj, field))()
            if model is Product and field == 'category':
                names = {r.sheet for r in obj.materialrecipe_set.all() if r.sheet}
                if obj.category:
                    names.add(obj.category)
                value = ', '.join(sorted(names)) or '—'
            if isinstance(value, bool): value = 'Evet' if value else 'Hayır'
            if isinstance(value,list): value = ' · '.join(str(v) for v in value) if value else 'İnceleme notu yok'
            if isinstance(value,dict): value = f"{value.get('rows',0)} satır · {value.get('rows_with_issues',0)} inceleme"
            values.append(value)
        detail_names={'urunler':'product_detail','receteler':'recipe_detail','excel-aktarimlari':'catalog_report','uretim-akislari':'flow_detail','uretim-isleri':'production_job'}
        detail=reverse(detail_names[slug],args=[obj.pk]) if slug in detail_names else ''
        editable=model is not ProductionJob or obj.state=='draft'
        rows.append({'pk':obj.pk, 'values':values,'detail':detail,'editable':editable,'product_detail':detail if model is Product else ''})
    return render(request,'erp/list.html', {'title':title,'slug':slug,'has_detail':slug in ['urunler','receteler','excel-aktarimlari','uretim-akislari','uretim-isleri'],'headers':headers,'rows':rows,'page':page,'q':search,'groups':groups,'category':category,'can_add':model not in READ_ONLY_MODELS and permitted(request.user,'add',model),'can_edit':model not in READ_ONLY_MODELS and model is not StockMovement and permitted(request.user,'change',model)})


@login_required
def product_detail(request, pk):
    if not request.user.has_perm('erp.view_product'):
        raise PermissionDenied
    product = get_object_or_404(main_products().prefetch_related('materialrecipe_set'), pk=pk)
    group_names = {r.sheet for r in product.materialrecipe_set.all() if r.sheet}
    if product.category:
        group_names.add(product.category)
    can_view_components = all(request.user.has_perm('erp.' + permission) for permission in (
        'view_materialrecipe', 'view_materialrequirement',
    ))
    tab = request.GET.get('tab', 'components' if can_view_components else 'overview')
    if tab not in ('overview', 'components'):
        raise Http404
    if tab == 'components' and not can_view_components:
        raise PermissionDenied
    recipes = []
    selected_recipe = None
    sections = []
    if can_view_components:
        recipes = list(MaterialRecipe.objects.filter(product=product).select_related('source').order_by('-source_id', 'sheet', 'pk'))
        selected = request.GET.get('recipe', '')
        if selected:
            selected_recipe = next((r for r in recipes if str(r.pk) == selected), None)
            if selected_recipe is None:
                raise Http404
        elif recipes:
            selected_recipe = recipes[0]
        if selected_recipe and tab == 'components':
            from decimal import Decimal
            from .production import material_plan
            rows, _ = material_plan(selected_recipe, Decimal('1'))
            sections = [
                {'title': 'Yarı mamuller', 'rows': [r for r in rows if r['line'].component_kind == 'Yarı Mamul']},
                {'title': 'Hammaddeler', 'rows': [r for r in rows if r['line'].component_kind == 'Hammadde']},
                {'title': 'Diğer bileşenler', 'rows': [r for r in rows if r['line'].component_kind not in ('Yarı Mamul', 'Hammadde')]},
            ]
    return render(request, 'erp/product.html', {
        'product': product, 'tab': tab, 'can_view_components': can_view_components,
        'recipes': recipes, 'selected_recipe': selected_recipe, 'sections': sections,
        'slug': 'urunler', 'display_group': ', '.join(sorted(group_names)) or '—',
    })

@login_required
@transaction.atomic
def edit(request, slug, pk=None):
    model, title, _ = module(slug)
    if model in READ_ONLY_MODELS: raise PermissionDenied
    if model is StockMovement and pk is not None: raise PermissionDenied
    action = 'change' if pk is not None else 'add'
    if not permitted(request.user,action,model): raise PermissionDenied
    queryset=model.objects.select_for_update() if model is ProductionJob else model.objects.all()
    obj = get_object_or_404(queryset,pk=pk) if pk is not None else model()
    if model is ProductionJob and pk is not None and obj.state!='draft':
        raise PermissionDenied('Başlatılmış iş emrinin tanımı değiştirilemez.')
    Form = modelform_factory(model, exclude=['created_by','created_at'], widgets={'date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'due_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')})
    form = Form(request.POST or None, instance=obj)
    # Do not leak names of related records through selectors without view permission.
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField) and not permitted(request.user,'view',field.queryset.model):
            field.queryset = field.queryset.none()
    if model is ProductionJob:
        form.fields['preceding_jobs'].queryset = form.fields['preceding_jobs'].queryset.exclude(pk=obj.pk)
    if request.method == 'POST' and form.is_valid():
        record = form.save(commit=False)
        if model in (StockMovement,ProductionJob) and not pk: record.created_by = request.user
        try:
            with transaction.atomic():
                record.save()
                form.save_m2m()
        except ValidationError as exc:
            form.add_error(None, exc)
        except OperationalError:
            form.add_error(None, 'Veri tabanı şu anda meşgul. Kaydın oluşup oluşmadığını kontrol edip tekrar deneyin.')
        else:
            messages.success(request, 'Kayıt başarıyla kaydedildi.')
            if model is ProductionJob: return redirect('production_job',pk=record.pk)
            return redirect('listing',slug=slug)
    return render(request,'erp/form.html',{'form':form,'title':title,'slug':slug,'editing':pk is not None})


class CatalogUploadForm(forms.Form):
    file = forms.FileField(label='Ürün ve ara mamul Excel dosyası (.xlsx)')
    def clean_file(self):
        file=self.cleaned_data['file']
        if not file.name.lower().endswith('.xlsx') or file.size>8*1024*1024:
            raise forms.ValidationError('En fazla 8 MB boyutunda bir .xlsx dosyası seçin.')
        return file

@login_required
def catalog_upload(request):
    from .catalog import import_catalog
    if not request.user.has_perm('erp.add_catalogimport'):raise PermissionDenied
    form=CatalogUploadForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        file=form.cleaned_data['file']
        try:
            batch,created=import_catalog(file.read(),file.name,request.user)
        except (ValidationError,OperationalError,IntegrityError) as exc:
            form.add_error(None,exc if isinstance(exc,ValidationError) else 'Veri tabanı meşgul; aktarım kaydını kontrol edip tekrar deneyin.')
        else:
            messages.success(request,'Excel aktarıldı. Boş hücreler doldurulmadı.' if created else 'Bu dosya daha önce aktarıldı; kayıtlar çoğaltılmadı.')
            return redirect('catalog_report',pk=batch.pk)
    return render(request,'erp/upload.html',{'form':form})

@login_required
def catalog_report(request,pk):
    from .models import CatalogImport
    if not all(request.user.has_perm('erp.'+p) for p in ['view_catalogimport','view_materialrecipe','view_materialrequirement']):raise PermissionDenied
    batch=get_object_or_404(CatalogImport,pk=pk)
    rows=batch.materialrequirement_set.exclude(issues=[]).order_by('sheet','row_number')
    page=Paginator(rows,50).get_page(request.GET.get('page'))
    return render(request,'erp/import_report.html',{'batch':batch,'page':page})

@login_required
def recipe_detail(request,pk):
    from decimal import Decimal,InvalidOperation
    from .production import material_plan
    if not request.user.has_perm('erp.view_materialrecipe') or not request.user.has_perm('erp.view_materialrequirement'):raise PermissionDenied
    recipe=get_object_or_404(MaterialRecipe.objects.select_related('source','product'),pk=pk)
    error=''
    try:
        quantity=Decimal(request.GET.get('quantity','1').replace(',','.'))
        if not quantity.is_finite() or quantity<=0 or quantity>=Decimal('1e9') or quantity.as_tuple().exponent < -3:raise InvalidOperation
    except (InvalidOperation,ValueError):
        quantity=None
        error='Pozitif, en fazla üç ondalıklı ve bir milyardan küçük bir üretim miktarı girin.'
    rows,problems=material_plan(recipe,quantity) if quantity is not None else ([],[])
    return render(request,'erp/recipe.html',{'recipe':recipe,'quantity':quantity,'rows':rows,'problems':problems,'error':error})

@login_required
def flow_detail(request,pk):
    from .models import ProductionFlow
    if not request.user.has_perm('erp.view_productionflow'):raise PermissionDenied
    flow=get_object_or_404(ProductionFlow,pk=pk)
    return render(request,'erp/flow.html',{'flow':flow})

@login_required
def production_job(request,pk):
    from .production import advance_job,material_plan
    if not request.user.has_perm('erp.view_productionjob'):raise PermissionDenied
    job=get_object_or_404(ProductionJob.objects.select_related('flow','product','recipe'),pk=pk)
    if request.method=='POST':
        if not request.user.has_perm('erp.advance_productionjob'):raise PermissionDenied
        try:
            message=advance_job(job.pk,request.user,request.POST.get('expected',''))
        except ValidationError as exc:
            for message in exc.messages:messages.error(request,message)
        except OperationalError:
            messages.error(request,'Veri tabanı meşgul; aşamanın durumunu kontrol edin.')
        else:messages.success(request,message)
        return redirect('production_job',pk=pk)
    steps=list(job.steps.select_related('completed_by').all())
    current=next((s for s in steps if not s.completed_at),None)
    rows,problems=([],[])
    if job.recipe_id and request.user.has_perm('erp.view_materialrequirement') and request.user.has_perm('erp.view_materialrecipe'):
        rows,problems=material_plan(job.recipe,job.quantity)
    return render(request,'erp/job.html',{'job':job,'steps':steps,'current':current,'rows':rows,'problems':problems})
