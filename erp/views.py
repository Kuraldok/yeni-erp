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
from .models import Product, Partner, Order, OrderLine, StockMovement, ProductionJob, ProductionRoute, MaterialRecipe, MaterialRequirement, stock_balance
from .registry import MODULES, READ_ONLY_MODELS
from .products import main_products, order_product_cards
from .group_filters import available_groups, can_filter_groups, filter_by_group
from .templatetags.erp_numbers import requirement_list_amount, count_amount
from .models import ConsumptionRecipe, ConsumptionRecipeLine, AuditEvent, QuantityReview, ProductionPosting
from .quantities import is_count_unit, quantity_fields

@login_required
def dashboard(request):
    category = request.GET.get('category', '').strip()
    def scoped(model):
        if category and not can_filter_groups(request.user, model):
            return model.objects.none()
        return filter_by_group(model.objects.all(), category, request.user)
    stats = []
    for model, title in [(Product,'Ürün'),(Partner,'Müşteri / tedarikçi'),(Order,'Sipariş'),(StockMovement,'Stok hareketi')]:
        if request.user.has_perm(f'erp.view_{model._meta.model_name}'):
            if category and not can_filter_groups(request.user, model):
                continue
            count = filter_by_group(main_products(), category, request.user).count() if model is Product else scoped(model).count()
            stats.append((title, count))
    balances = []
    if request.user.has_perm('erp.view_stockmovement') and request.user.has_perm('erp.view_product'):
        for product in scoped(Product).filter(active=True)[:50]:
            quantity = stock_balance(product)
            balances.append({'product':product, 'quantity':quantity, 'low':product.minimum_stock is not None and quantity < product.minimum_stock})
    recent = scoped(StockMovement).select_related('product','warehouse','created_by')[:8] if request.user.has_perm('erp.view_stockmovement') else []
    from .models import ProductionFlow, CatalogImport
    flows=scoped(ProductionFlow).prefetch_related('steps') if request.user.has_perm('erp.view_productionflow') else []
    catalog=scoped(CatalogImport).first() if request.user.has_perm('erp.view_catalogimport') else None
    return render(request, 'erp/dashboard.html', {'stats':stats,'balances':balances,'recent':recent,'flows':flows,'catalog':catalog,'groups':available_groups(request.user),'category':category,'can_filter_groups':True})

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
    if model is QuantityReview:
        permitted_labels = [item[0]._meta.label_lower for item in MODULES.values() if request.user.has_perm(f'erp.view_{item[0]._meta.model_name}')]
        qs = qs.filter(model_label__in=permitted_labels)
    category = request.GET.get('category', '').strip()
    can_filter = can_filter_groups(request.user, model)
    groups = available_groups(request.user) if can_filter else []
    if model is Product:
        qs = main_products()
        qs = qs.prefetch_related('materialrecipe_set')
    qs = filter_by_group(qs, category, request.user)
    order_filter = ''
    selected_order = None
    if model is OrderLine and request.GET.get('order'):
        if not request.user.has_perm('erp.view_order'):
            raise PermissionDenied
        from .orders import selection_pk
        order_id = selection_pk(request.GET['order'])
        qs = qs.filter(order_id=order_id) if order_id is not None else qs.none()
        order_filter = str(order_id) if order_id is not None else request.GET['order'][:19]
        selected_order = Order.objects.filter(pk=order_id).first() if order_id is not None else None
    recipe_filter = ''
    if model is ConsumptionRecipeLine and request.GET.get('recipe'):
        from .orders import selection_pk
        recipe_id = selection_pk(request.GET['recipe'])
        qs = qs.filter(recipe_id=recipe_id) if recipe_id is not None else qs.none()
        recipe_filter = str(recipe_id) if recipe_id else ''
    search = request.GET.get('q','').strip()
    if search:
        condition = Q()
        for field in model._meta.fields:
            if isinstance(field, (CharField, TextField)):
                condition |= Q(**{field.name+'__icontains':search})
        if condition: qs = qs.filter(condition)
    if model is Product:
        qs = order_product_cards(qs)
    else:
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
        quantity_units = {field:unit for field, _, unit in quantity_fields(obj)}
        for field in fields:
            value = getattr(obj, f'get_{field}_display', lambda: getattr(obj, field))()
            if field in quantity_units:
                value = count_amount(value, quantity_units[field])
            if model is MaterialRequirement:
                if field == 'quantity':
                    value = requirement_list_amount(obj.quantity, obj.unit)
                elif field == 'issues':
                    from .routing import requirement_issues
                    value = requirement_issues(obj)
            if model is Product and field == 'category':
                names = {r.sheet for r in obj.materialrecipe_set.all() if r.sheet}
                if obj.category:
                    names.add(obj.category)
                value = ', '.join(sorted(names)) or '—'
            if isinstance(value, bool): value = 'Evet' if value else 'Hayır'
            if isinstance(value,list): value = ' · '.join(str(v) for v in value) if value else 'İnceleme notu yok'
            if isinstance(value,dict): value = f"{value.get('rows',0)} satır · {value.get('rows_with_issues',0)} inceleme"
            values.append(value)
        detail_names={'urunler':'product_detail','rotalar':'route_detail','receteler':'recipe_detail','excel-aktarimlari':'catalog_report','uretim-akislari':'flow_detail','uretim-isleri':'production_job','tuketim-receteleri':'consumption_recipe_detail','islem-gecmisi':'audit_detail','uretim-stok-fisleri':'production_posting_detail'}
        detail=reverse(detail_names[slug],args=[obj.pk]) if slug in detail_names else ''
        editable=model is not ProductionJob or obj.state=='draft'
        if model is OrderLine:
            editable=obj.order.status=='draft' and obj.status=='active'
        if model is ConsumptionRecipe:
            editable=not obj.locked
        if model is ConsumptionRecipeLine:
            editable=not obj.recipe.locked
        cancel_url = reverse('cancel_order_line',args=[obj.pk]) if model is OrderLine and editable and request.user.has_perm('erp.cancel_orderline') else ''
        rows.append({'pk':obj.pk, 'values':values,'detail':detail,'editable':editable,'cancel_url':cancel_url,'product_detail':detail if model is Product else ''})
    return render(request,'erp/list.html', {'title':title,'slug':slug,'has_detail':slug in ['urunler','rotalar','receteler','excel-aktarimlari','uretim-akislari','uretim-isleri','tuketim-receteleri','islem-gecmisi','uretim-stok-fisleri'],'headers':headers,'rows':rows,'page':page,'q':search,'groups':groups,'category':category,'can_filter_groups':can_filter,'order_filter':order_filter,'selected_order':selected_order,'recipe_filter':recipe_filter,'can_cancel':model is OrderLine and request.user.has_perm('erp.cancel_orderline'),'can_add':model not in READ_ONLY_MODELS and permitted(request.user,'add',model),'can_edit':model not in READ_ONLY_MODELS and model is not StockMovement and permitted(request.user,'change',model)})


@login_required
def product_detail(request, pk):
    if not request.user.has_perm('erp.view_product'):
        raise PermissionDenied
    product = get_object_or_404(main_products().prefetch_related('materialrecipe_set'), pk=pk)
    group_names = {r.sheet for r in product.materialrecipe_set.all() if r.sheet}
    if product.category:
        group_names.add(product.category)
    from .routing import is_grill_category
    is_grill = any(is_grill_category(name) for name in group_names)
    direct_routes = product.productionroute_set.filter(active=True).select_related('flow') if request.user.has_perm('erp.view_productionroute') else []
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
            from .routing import is_semifinished_component
            rows, _ = material_plan(selected_recipe, Decimal('1'))
            sections = [
                {'title': 'Yarı mamuller', 'rows': [r for r in rows if is_semifinished_component(r['line'].component_kind, r['line'].sheet)]},
                {'title': 'Hammaddeler', 'rows': [r for r in rows if r['line'].component_kind == 'Hammadde']},
                {'title': 'Diğer bileşenler', 'rows': [r for r in rows if r['line'].component_kind != 'Hammadde' and not is_semifinished_component(r['line'].component_kind, r['line'].sheet)]},
            ]
    return render(request, 'erp/product.html', {
        'product': product, 'tab': tab, 'can_view_components': can_view_components,
        'recipes': recipes, 'selected_recipe': selected_recipe, 'sections': sections,
        'slug': 'urunler', 'display_group': ', '.join(sorted(group_names)) or '—',
        'is_grill': is_grill, 'direct_routes': direct_routes,
    })

@login_required
@transaction.atomic
def edit(request, slug, pk=None):
    model, title, _ = module(slug)
    if model is Order:
        from .order_views import edit_order
        return edit_order(request, pk)
    if model is OrderLine:
        from .order_views import edit_order_line
        return edit_order_line(request, pk)
    if model in READ_ONLY_MODELS: raise PermissionDenied
    if model is StockMovement and pk is not None: raise PermissionDenied
    action = 'change' if pk is not None else 'add'
    if not permitted(request.user,action,model): raise PermissionDenied
    queryset=model.objects.select_for_update() if model in (ProductionJob,Product,ConsumptionRecipe) else model.objects.all()
    obj = get_object_or_404(queryset,pk=pk) if pk is not None else model()
    if model is ProductionJob and pk is not None and obj.state!='draft':
        raise PermissionDenied('Başlatılmış iş emrinin tanımı değiştirilemez.')
    if model is ConsumptionRecipe and pk is not None and obj.locked:
        raise PermissionDenied('Kullanılmış reçete için yeni sürüm oluşturun.')
    if model is ConsumptionRecipeLine and pk is not None and obj.recipe.locked:
        raise PermissionDenied('Kullanılmış reçetenin girdileri korunur.')
    Form = modelform_factory(model, exclude=['created_by','created_at'], widgets={'date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'due_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')})
    initial = {}
    if model is ConsumptionRecipe and pk is None and request.method == 'GET' and request.GET.get('product'):
        from .orders import selection_pk
        if not request.user.has_perm('erp.view_product'):
            raise PermissionDenied
        product = get_object_or_404(Product,pk=selection_pk(request.GET['product']))
        from django.db.models import Max
        version = (ConsumptionRecipe.objects.filter(product=product).aggregate(n=Max('version'))['n'] or 0)+1
        initial={'product':product.pk,'unit':product.unit,'name':f'{product.code} · tüketim','version':version}
    if model is ConsumptionRecipeLine and pk is None and request.method == 'GET' and request.GET.get('recipe'):
        from .orders import selection_pk
        if not request.user.has_perm('erp.view_consumptionrecipe'):
            raise PermissionDenied
        recipe = get_object_or_404(ConsumptionRecipe,pk=selection_pk(request.GET['recipe']))
        if recipe.locked:
            raise PermissionDenied('Kullanılmış reçetenin girdileri korunur.')
        initial['recipe'] = recipe.pk
    if model is ProductionJob and request.method=='GET' and pk is None and request.GET.get('route'):
        if not all(request.user.has_perm('erp.'+p) for p in ['view_productionroute','view_product','view_productionflow']):
            raise PermissionDenied
        try:
            route_id=int(request.GET['route'])
        except (TypeError,ValueError):
            raise Http404
        route=get_object_or_404(ProductionRoute.objects.select_related('product','flow'),pk=route_id,active=True)
        if route.supply_method=='purchase' or not route.flow_id:
            raise PermissionDenied('Bu rota üretim işi açmaya uygun değil.')
        initial={'route':route.pk,'product':route.product_id,'flow':route.flow_id,'unit':route.product.unit}
    form = Form(request.POST or None, instance=obj, initial=initial)
    # Do not leak names of related records through selectors without view permission.
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField) and not permitted(request.user,'view',field.queryset.model):
            field.queryset = field.queryset.none()
    if model is ProductionJob:
        form.fields['preceding_jobs'].queryset = form.fields['preceding_jobs'].queryset.exclude(pk=obj.pk)
        if not request.user.has_perm('erp.track_production_without_stock') and (pk is None or obj.stock_mode != 'tracking'):
            form.fields['stock_mode'].choices = [('inventory','Üretim ve stok')]
    quantity_field = 'minimum_stock' if model is Product else 'quantity' if model in (StockMovement,ProductionJob,ConsumptionRecipeLine) else ''
    product_units = {}
    if model is StockMovement:
        product_units = {str(pk):unit for pk,unit in form.fields['product'].queryset.values_list('pk','unit')}
    if quantity_field:
        unit = product_units.get(request.POST.get('product',''),'') if model is StockMovement else request.POST.get('unit','') if request.method == 'POST' else form.initial.get('unit','')
        if is_count_unit(unit):
            form.fields[quantity_field].widget.attrs.update(step='1',min='0' if quantity_field=='minimum_stock' else '1')
            value = form.initial.get(quantity_field)
            if value is not None and value == int(value):
                form.initial[quantity_field] = int(value)
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
            if model is ConsumptionRecipe: return redirect('consumption_recipe_detail',pk=record.pk)
            if model is ConsumptionRecipeLine: return redirect('consumption_recipe_detail',pk=record.recipe_id)
            return redirect('listing',slug=slug)
    return render(request,'erp/form.html',{'form':form,'title':title,'slug':slug,'editing':pk is not None,'quantity_field':quantity_field,'product_units':product_units})


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
    from .catalog import HEADERS
    from .routing import requirement_issues
    if not all(request.user.has_perm('erp.'+p) for p in ['view_catalogimport','view_materialrecipe','view_materialrequirement']):raise PermissionDenied
    batch=get_object_or_404(CatalogImport,pk=pk)
    category=request.GET.get('category','').strip()
    selected=filter_by_group(batch.materialrequirement_set.all(),category,request.user)
    rows=selected.exclude(issues=[])
    obsolete=[line.pk for line in rows if not requirement_issues(line)]
    rows=rows.exclude(pk__in=obsolete).order_by('sheet','row_number')
    page=Paginator(rows,50).get_page(request.GET.get('page'))
    for line in page:
        line.display_issues=requirement_issues(line)
    selected_rows=list(selected) if category else []
    blank_fields={label:sum(line.raw.get(label) in (None,'') for line in selected_rows) for label in HEADERS.values()} if category else batch.summary.get('blank_fields',{})
    codes={code for line in selected_rows for code in (line.parent_code,line.component_code)}
    conflicts=[code for code in batch.summary.get('conflict_codes',[]) if not category or code in codes]
    report_stats={'sheets':selected.values('sheet').distinct().count(),'rows':selected.count(),'issues':rows.count()}
    return render(request,'erp/import_report.html',{'batch':batch,'page':page,'report_stats':report_stats,'blank_fields':blank_fields,'conflict_codes':conflicts,'groups':available_groups(request.user),'category':category,'can_filter_groups':True})

@login_required
def recipe_detail(request,pk):
    from decimal import Decimal,InvalidOperation
    from .production import material_plan
    if not request.user.has_perm('erp.view_materialrecipe') or not request.user.has_perm('erp.view_materialrequirement'):raise PermissionDenied
    recipe=get_object_or_404(MaterialRecipe.objects.select_related('source','product'),pk=pk)
    category=request.GET.get('category','').strip()
    matches_group=filter_by_group(MaterialRecipe.objects.filter(pk=pk),category,request.user).exists()
    error=''
    try:
        quantity=Decimal(request.GET.get('quantity','1').replace(',','.'))
        if not quantity.is_finite() or quantity<=0 or quantity>=Decimal('1e9') or quantity.as_tuple().exponent < -3:raise InvalidOperation
    except (InvalidOperation,ValueError):
        quantity=None
        error='Pozitif, en fazla üç ondalıklı ve bir milyardan küçük bir üretim miktarı girin.'
    if quantity is not None and recipe.product_id and is_count_unit(recipe.product.unit) and quantity != quantity.to_integral_value():
        quantity=None
        error='Adet birimindeki üretim miktarı tam sayı olmalıdır.'
    rows,problems=material_plan(recipe,quantity) if quantity is not None and matches_group else ([],[])
    return render(request,'erp/recipe.html',{'recipe':recipe,'quantity':quantity,'rows':rows,'problems':problems,'error':error,'groups':available_groups(request.user),'category':category,'can_filter_groups':True,'matches_group':matches_group})

@login_required
def flow_detail(request,pk):
    from .models import ProductionFlow
    if not request.user.has_perm('erp.view_productionflow'):raise PermissionDenied
    flow=get_object_or_404(ProductionFlow,pk=pk)
    return render(request,'erp/flow.html',{'flow':flow})


@login_required
def route_detail(request,pk):
    if not request.user.has_perm('erp.view_productionroute'):
        raise PermissionDenied
    route=get_object_or_404(ProductionRoute.objects.select_related('product','flow'),pk=pk)
    return render(request,'erp/route.html',{'route':route,'slug':'rotalar'})

@login_required
def production_job(request,pk):
    from .production import advance_job,material_plan
    if not request.user.has_perm('erp.view_productionjob'):raise PermissionDenied
    job=get_object_or_404(ProductionJob.objects.select_related('flow','product','recipe'),pk=pk)
    steps=list(job.steps.select_related('completed_by').all())
    current=next((s for s in steps if not s.completed_at),None)
    last_inventory_step=job.stock_mode=='inventory' and job.state=='running' and sum(s.completed_at is None for s in steps)==1
    from .inventory_views import ProductionCompletionForm
    completion_form=ProductionCompletionForm(request.POST if request.method=='POST' else None,unit=job.unit,initial={'actual_quantity':job.quantity}) if last_inventory_step else None
    if request.method=='POST':
        if not request.user.has_perm('erp.advance_productionjob'):raise PermissionDenied
        if completion_form is not None and not completion_form.is_valid():
            return render(request,'erp/job.html',{'job':job,'steps':steps,'current':current,'completion_form':completion_form,'stock_inputs':job.stock_inputs.select_related('product'),'rows':[],'problems':[]})
        try:
            message=advance_job(job.pk,request.user,request.POST.get('expected',''),actual_quantity=completion_form.cleaned_data['actual_quantity'] if completion_form is not None else None)
        except ValidationError as exc:
            for message in exc.messages:messages.error(request,message)
        except OperationalError:
            messages.error(request,'Veri tabanı meşgul; aşamanın durumunu kontrol edin.')
        else:messages.success(request,message)
        return redirect('production_job',pk=pk)
    rows,problems=([],[])
    if job.recipe_id and request.user.has_perm('erp.view_materialrequirement') and request.user.has_perm('erp.view_materialrecipe'):
        rows,problems=material_plan(job.recipe,job.quantity)
    return render(request,'erp/job.html',{'job':job,'steps':steps,'current':current,'rows':rows,'problems':problems,'completion_form':completion_form,'stock_inputs':job.stock_inputs.select_related('product')})
