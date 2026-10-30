from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .models import Order, OrderLine
from .order_forms import OrderForm, OrderLineForm, OrderLineFormSet
from .orders import product_selection, selection_pk


def permitted(user, action, model):
    return user.has_perm(f'erp.{action}_{model._meta.model_name}')


def selection_context(user, kind):
    return product_selection(kind) if user.has_perm('erp.view_product') else {'kind':kind, 'products':[], 'groups':[]}


@login_required
@require_GET
def product_options(request):
    if not request.user.has_perm('erp.view_product') or not any(request.user.has_perm('erp.' + code) for code in ('add_orderline', 'change_orderline')):
        raise PermissionDenied
    kind = request.GET.get('kind', '')
    if request.GET.get('order'):
        if not request.user.has_perm('erp.view_order'):
            raise PermissionDenied
        order_id = selection_pk(request.GET['order'])
        order = get_object_or_404(Order, pk=order_id) if order_id is not None else None
        if order is None or order.status != 'draft':
            return JsonResponse({'error':'Kalem eklemek için taslak sipariş seçin.'}, status=400)
        kind = order.kind
    if kind not in ('sale', 'purchase'):
        return JsonResponse({'error':'Satış veya satın alma türünü seçin.'}, status=400)
    return JsonResponse(product_selection(kind))


@login_required
@transaction.atomic
def edit_order(request, pk=None):
    if not permitted(request.user, 'change' if pk is not None else 'add', Order):
        raise PermissionDenied
    order = get_object_or_404(Order.objects.select_for_update(), pk=pk) if pk is not None else Order()
    original_pk = order.pk
    original_status = order.status
    kind = request.POST.get('kind', '') if request.method == 'POST' else order.kind or 'sale'
    group = request.POST.get('product_group', '') if request.method == 'POST' else request.GET.get('category', '')
    form = OrderForm(request.POST if request.method == 'POST' else None, instance=order, user=request.user, initial={'kind':kind})
    can_view_lines = permitted(request.user, 'view', OrderLine)
    can_add_lines = permitted(request.user, 'add', OrderLine)
    can_edit_lines = permitted(request.user, 'change', OrderLine)
    editable = original_status == 'draft' and can_view_lines and request.user.has_perm('erp.view_product') and (can_add_lines or can_edit_lines)
    has_lines = request.method == 'POST' and any(key.startswith('lines-') for key in request.POST)
    if has_lines and not editable:
        raise PermissionDenied('Bu siparişin kalemlerini değiştirme yetkiniz yok veya sipariş taslak değil.')
    formset = None
    if editable:
        # Validate lines against the locked draft, separately from the header's requested status.
        line_order = Order.objects.get(pk=pk) if pk is not None else Order()
        line_order.kind = kind
        queryset = OrderLine.objects.select_for_update().filter(order_id=pk,status='active').order_by('pk') if pk is not None else OrderLine.objects.none()
        formset = OrderLineFormSet(
            request.POST if has_lines else None, instance=line_order, queryset=queryset,
            prefix='lines', user=request.user,
            form_kwargs={'user':request.user, 'order_kind':kind, 'product_group':group},
        )
        formset.extra = 1 if can_add_lines else 0
    if request.method == 'POST' and 'refresh_products' not in request.POST:
        valid = form.is_valid()
        if has_lines:
            valid = formset.is_valid() and valid
        if valid:
            record = form.save(commit=False)
            desired_status = record.status
            initial_pks = [line.instance.pk for line in formset.forms] if has_lines else []
            try:
                with transaction.atomic():
                    if has_lines:
                        record.status = 'draft'
                    record.save()
                    if has_lines:
                        formset.instance = record
                        formset.save()
                        record.status = desired_status
                        record.full_clean()
                        record.save()
            except (ValidationError, OperationalError, IntegrityError) as exc:
                if original_pk is None:
                    record.pk = None
                    record._state.adding = True
                record.status = desired_status
                if has_lines:
                    for line, previous_pk in zip(formset.forms, initial_pks):
                        if previous_pk is None:
                            line.instance.pk = None
                            line.instance._state.adding = True
                form.add_error(None, ValidationError(exc.messages) if isinstance(exc,ValidationError) else 'Sipariş kaydedilemedi. Numaranın kullanılmadığını ve güncel kayıtları kontrol edip tekrar deneyin.')
            else:
                messages.success(request, 'Sipariş ve kalemleri kaydedildi.' if has_lines else 'Sipariş kaydedildi.')
                return redirect('listing', slug='siparisler')
    existing_lines = order.lines.filter(status='active').select_related('product').order_by('pk') if original_pk is not None and can_view_lines else []
    cancelled_lines = order.lines.filter(status='cancelled').select_related('product','cancelled_by') if original_pk is not None and can_view_lines else []
    selection = selection_context(request.user, kind) if editable else {'kind':kind, 'products':[], 'groups':[]}
    return render(request, 'erp/order_form.html', {
        'form':form, 'formset':formset, 'order':order, 'editing':pk is not None,
        'slug':'siparisler', 'title':'Siparişler', 'existing_lines':existing_lines,
        'can_add_lines':editable and can_add_lines, 'product_group':group,
        'selection':selection, 'can_view_lines':can_view_lines,
        'can_manage_lines':editable,
        'cancelled_lines':cancelled_lines,
    })


@login_required
@transaction.atomic
def edit_order_line(request, pk=None):
    if not permitted(request.user, 'change' if pk is not None else 'add', OrderLine):
        raise PermissionDenied
    original = get_object_or_404(OrderLine, pk=pk) if pk is not None else None
    selected = request.POST.get('order', '') if request.method == 'POST' else request.GET.get('order', str(original.order_id) if original else '')
    selected_id = selection_pk(selected)
    ids = {original.order_id} if original else set()
    if selected_id is not None and request.user.has_perm('erp.view_order'):
        ids.add(selected_id)
    locked = {order.pk:order for order in Order.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
    line = get_object_or_404(OrderLine.objects.select_for_update(), pk=pk) if original else OrderLine()
    if original and line.order_id not in locked:
        return HttpResponse('Sipariş kalemi başka bir siparişe taşındı. Sayfayı yenileyin.', status=409)
    if original and locked[line.order_id].status != 'draft':
        raise PermissionDenied('Onaylı siparişin kalemleri değiştirilemez. Önce siparişi taslağa alın.')
    if original and line.status == 'cancelled':
        raise PermissionDenied('İptal edilmiş kalem değiştirilemez.')
    selected_order = locked.get(selected_id) if selected_id is not None and request.user.has_perm('erp.view_order') else None
    kind = selected_order.kind if selected_order else ''
    group = request.POST.get('product_group', '') if request.method == 'POST' else request.GET.get('category', '')
    form = OrderLineForm(request.POST if request.method == 'POST' else None,
        instance=line, initial={'order':selected_order.pk} if selected_order else {},
        user=request.user, order_kind=kind, product_group=group)
    if request.method == 'POST' and 'refresh_products' not in request.POST and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except (ValidationError, OperationalError, IntegrityError) as exc:
            form.add_error(None, ValidationError(exc.messages) if isinstance(exc,ValidationError) else 'Kalem kaydedilemedi. Siparişin güncel durumunu kontrol edip tekrar deneyin.')
        else:
            messages.success(request, 'Sipariş kalemi kaydedildi.')
            return redirect('listing', slug='siparis-kalemleri')
    return render(request, 'erp/order_line_form.html', {
        'form':form, 'editing':pk is not None, 'slug':'siparis-kalemleri',
        'title':'Sipariş kalemleri', 'selection':selection_context(request.user,kind),
        'product_group':group,
    })


class CancellationForm(forms.Form):
    reason = forms.CharField(label='İptal nedeni',max_length=2000,widget=forms.Textarea)


@login_required
@require_http_methods(['GET','POST'])
def cancel_order_line(request, pk):
    if not all(request.user.has_perm('erp.'+code) for code in ('cancel_orderline','view_orderline','view_order')):
        raise PermissionDenied
    line = get_object_or_404(OrderLine.objects.select_related('order','product','cancelled_by'),pk=pk)
    form = CancellationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        from .operations import cancel_line
        try:
            _, created = cancel_line(pk,request.user,form.cleaned_data['reason'])
        except ValidationError as exc:
            form.add_error(None,exc)
        except (OperationalError,IntegrityError):
            form.add_error(None,'Kayıt meşgul. Kalemin güncel durumunu kontrol edin.')
        else:
            messages.success(request,'Kalem iptal edildi; geçmişi korundu.' if created else 'Kalem zaten iptal edilmiş. İşlem tekrarlanmadı.')
            return redirect(f'/modul/siparis-kalemleri/?order={line.order_id}')
    return render(request,'erp/cancel_line.html',{'line':line,'form':form,'slug':'siparis-kalemleri'})
