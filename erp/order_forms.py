from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory

from .group_filters import grouped_records
from .models import Order, OrderLine
from .orders import order_products, selection_pk


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['number', 'kind', 'partner', 'status', 'date', 'due_date', 'currency', 'note']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'due_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if not user.has_perm('erp.view_partner'):
            self.fields['partner'].queryset = self.fields['partner'].queryset.none()
        if not self.instance.pk:
            self.fields['status'].choices = [('draft', 'Taslak')]
            self.fields['status'].help_text = 'Yeni sipariş kalemleriyle birlikte taslak olarak kaydedilir.'


class OrderLineForm(forms.ModelForm):
    class Meta:
        model = OrderLine
        fields = ['order', 'product', 'quantity', 'unit_price']
        labels = {'quantity': 'Adet / miktar'}
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': '0.001', 'step': '0.001'}),
            'unit_price': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
        }

    def __init__(self, *args, user, order_kind='', product_group='', **kwargs):
        super().__init__(*args, **kwargs)
        if 'order' in self.fields:
            self.fields['order'].queryset = Order.objects.filter(status='draft') if user.has_perm('erp.view_order') else Order.objects.none()
        products = order_products(order_kind) if user.has_perm('erp.view_product') else order_products('')
        if product_group:
            grouped = grouped_records(products.model) if product_group == '__none__' else grouped_records(products.model, product_group)
            condition = ~Q(pk__in=grouped.values('pk')) if product_group == '__none__' else Q(pk__in=grouped.values('pk'))
            # A group change narrows new choices without dropping already selected valid rows.
            selected = self.data.get(self.add_prefix('product')) if self.is_bound else self.initial.get('product')
            selected_pk = selection_pk(selected)
            if selected_pk is not None:
                condition |= Q(pk=selected_pk)
            products = products.filter(condition)
        self.fields['product'].queryset = products
        self.fields['product'].label_from_instance = lambda product: f'{product} · {product.get_unit_display() or "Birim belirtilmedi"}'


class BaseOrderLineFormSet(BaseInlineFormSet):
    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def add_fields(self, form, index):
        super().add_fields(form, index)
        # A hidden line id must belong to this order, including on forged submissions.
        form.fields[self.model._meta.pk.name].queryset = self.queryset

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        ids = []
        for form in self.forms:
            identity = form.cleaned_data.get('id')
            if identity:
                ids.append(identity.pk)
            if not form.has_changed():
                continue
            action = 'change' if form.instance.pk else 'add'
            if not self.user.has_perm(f'erp.{action}_orderline'):
                raise ValidationError('Bu sipariş kalemi işlemi için yetkiniz yok.')
        if len(ids) != len(set(ids)):
            raise ValidationError('Aynı sipariş kalemi birden fazla satır olarak gönderilemez.')


OrderLineFormSet = inlineformset_factory(
    Order, OrderLine, form=OrderLineForm, formset=BaseOrderLineFormSet,
    fields=['product', 'quantity', 'unit_price'], extra=1, can_delete=False,
)
