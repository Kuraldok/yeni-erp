from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse

from .catalog import import_catalog
from .models import Order, OrderLine, Partner, Product, StockMovement
from .test_routing import workbook


class OrderEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('order-admin', password='Order-Tests-8924')
        cls.customer = Partner.objects.create(name='Müşteri', kind='customer')
        cls.supplier = Partner.objects.create(name='Tedarikçi', kind='supplier')
        import_catalog(workbook([('Banklar', [
            [1, 'P1', 'Ana bank', 'S1', 'Ahşap Bölüm', 'Yarı Mamul', 2, 'Adet'],
            [2, 'P1', 'Ana bank', 'R1', 'Pik', 'Hammadde', 3, 'KG'],
            [3, 'P1', 'Ana bank', 'F1', 'Bitmiş bileşen', 'Mamul', 1, 'Adet'],
            [4, 'P1', 'Ana bank', 'X1', 'Vida', 'Parça', 4, 'Adet'],
        ])]), 'order-products.xlsx')
        cls.main = Product.objects.get(code='P1')
        cls.manual = Product.objects.create(code='P2', name='Ana soba', kind='finished', category='Soba')
        cls.raw = Product.objects.get(code='R1')
        cls.semi = Product.objects.get(code='S1')
        cls.component = Product.objects.get(code='F1')
        cls.part = Product.objects.get(code='X1')

    def setUp(self):
        self.client.force_login(self.user)

    def header(self, number='TEST-1', kind='sale', **extra):
        return {'number':number, 'kind':kind,
            'partner':self.customer.pk if kind == 'sale' else self.supplier.pk,
            'status':'draft', 'date':'2026-10-30', 'due_date':'', 'currency':'TRY', 'note':''} | extra

    def line_payload(self, rows, initial=0):
        data = {'lines-TOTAL_FORMS':str(len(rows)), 'lines-INITIAL_FORMS':str(initial),
            'lines-MIN_NUM_FORMS':'0', 'lines-MAX_NUM_FORMS':'1000'}
        for i, row in enumerate(rows):
            for field in ('id', 'order', 'product', 'quantity', 'unit_price'):
                data[f'lines-{i}-{field}'] = str(row.get(field, ''))
        return data

    def create(self, kind='sale', **kwargs):
        return Order.objects.create(number=kwargs.get('number','EXISTING'), kind=kind,
            partner=self.customer if kind == 'sale' else self.supplier,
            date=date(2026, 10, 30), status=kwargs.get('status','draft'))

    def test_new_order_screen_contains_main_products_and_inline_rows(self):
        response = self.client.get(reverse('create', args=['siparisler']))
        self.assertEqual(response.status_code, 200)
        formset = response.context['formset']
        self.assertEqual(set(formset.forms[0].fields['product'].queryset.values_list('pk',flat=True)), {self.main.pk,self.manual.pk})
        self.assertContains(response, 'name="lines-0-product"')
        self.assertContains(response, 'data-add-line')
        self.assertContains(response, 'Yeni Sipariş')

    def test_multiple_lines_save_with_header_and_are_visible_in_line_list(self):
        rows = [{'product':self.main.pk,'quantity':'2','unit_price':'12.40'},
            {'product':self.manual.pk,'quantity':'3','unit_price':'7.10'}]
        response = self.client.post(reverse('create', args=['siparisler']), self.header() | self.line_payload(rows))
        self.assertRedirects(response, reverse('listing', args=['siparisler']))
        order = Order.objects.get(number='TEST-1')
        self.assertEqual(order.lines.count(), 2)
        self.assertEqual(order.total, Decimal('46.10'))
        response = self.client.get(reverse('listing', args=['siparis-kalemleri']), {'order':order.pk})
        self.assertEqual(response.context['page'].paginator.count, 2)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_purchase_accepts_only_raw_materials_and_decimal_quantity(self):
        response = self.client.post(reverse('create', args=['siparisler']), self.header(kind='purchase') |
            self.line_payload([{'product':self.raw.pk,'quantity':'1.375','unit_price':'20.00'}]))
        self.assertEqual(response.status_code, 302)
        line = OrderLine.objects.get()
        self.assertEqual(line.product_id, self.raw.pk)
        self.assertEqual(line.quantity, Decimal('1.375'))
        self.assertEqual(line.total, Decimal('27.50'))

    def test_forged_inline_products_are_rejected_for_both_types(self):
        for kind, product in [('sale',self.raw),('sale',self.semi),('sale',self.part),
                ('sale',self.component),('purchase',self.main),('purchase',self.semi)]:
            with self.subTest(kind=kind, product=product.code):
                response = self.client.post(reverse('create', args=['siparisler']), self.header(kind=kind) |
                    self.line_payload([{'product':product.pk,'quantity':1,'unit_price':2}]))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['formset'].errors[0].get('product'))
                self.assertEqual(Order.objects.count(), 0)
                self.assertEqual(OrderLine.objects.count(), 0)

    def test_one_bad_row_does_not_leave_a_header_or_other_lines(self):
        rows = [{'product':self.main.pk,'quantity':2,'unit_price':10},
            {'product':self.manual.pk,'quantity':0,'unit_price':10}]
        response = self.client.post(reverse('create', args=['siparisler']), self.header() | self.line_payload(rows))
        self.assertTrue(response.context['formset'].errors[1].get('quantity'))
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderLine.objects.count(), 0)
        self.assertEqual(response.context['formset'].forms[0]['quantity'].value(), '2')

    def test_save_failure_rolls_back_all_rows_and_resets_unsaved_ids(self):
        original_save = OrderLine.save
        calls = 0
        def fail_second(line, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OperationalError('simulated busy database')
            return original_save(line, *args, **kwargs)
        rows = [{'product':self.main.pk,'quantity':2,'unit_price':10},
            {'product':self.manual.pk,'quantity':1,'unit_price':5}]
        with patch.object(OrderLine, 'save', new=fail_second):
            response = self.client.post(reverse('create', args=['siparisler']), self.header() | self.line_payload(rows))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderLine.objects.count(), 0)
        self.assertIsNone(response.context['form'].instance.pk)
        self.assertTrue(all(form.instance.pk is None for form in response.context['formset'].forms))

    def test_line_page_and_inline_editor_update_the_same_line(self):
        order = self.create()
        response = self.client.post(reverse('create', args=['siparis-kalemleri']),
            {'order':order.pk,'product':self.main.pk,'quantity':2,'unit_price':10})
        self.assertEqual(response.status_code, 302)
        line = order.lines.get()
        url = reverse('edit', args=['siparisler',order.pk])
        self.assertEqual(self.client.get(url).context['formset'].initial_form_count(), 1)
        response = self.client.post(url, self.header(number=order.number) |
            self.line_payload([{'id':line.pk,'order':order.pk,'product':self.main.pk,'quantity':3,'unit_price':11}],initial=1))
        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.unit_price, 11)
        self.assertEqual(order.total, Decimal('33.00'))

    def test_line_page_filters_by_selected_order_and_rejects_wrong_product(self):
        sale = self.create(number='SALE')
        purchase = self.create('purchase', number='PURCHASE')
        url = reverse('create', args=['siparis-kalemleri'])
        response = self.client.get(url, {'order':purchase.pk})
        self.assertEqual(list(response.context['form'].fields['product'].queryset), [self.raw])
        self.assertEqual(self.client.post(url, {'order':purchase.pk,'product':self.main.pk,'quantity':1,'unit_price':2}).status_code, 200)
        self.assertEqual(self.client.post(url, {'order':sale.pk,'product':self.raw.pk,'quantity':1,'unit_price':2}).status_code, 200)
        self.assertEqual(OrderLine.objects.count(), 0)
        self.assertEqual(self.client.post(url, {'order':purchase.pk,'product':self.raw.pk,'quantity':'0.5','unit_price':2}).status_code, 302)

    def test_kind_change_with_incompatible_existing_lines_is_blocked(self):
        order = self.create()
        line = OrderLine.objects.create(order=order, product=self.main, quantity=2, unit_price=3)
        response = self.client.post(reverse('edit', args=['siparisler',order.pk]), self.header(number=order.number,kind='purchase'))
        self.assertTrue(response.context['form'].errors.get('kind'))
        order.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(order.kind, 'sale')
        self.assertEqual(line.product_id, self.main.pk)
        empty = self.create(number='EMPTY')
        response = self.client.post(reverse('edit', args=['siparisler',empty.pk]), self.header(number='EMPTY',kind='purchase'))
        self.assertEqual(response.status_code, 302)
        empty.refresh_from_db()
        self.assertEqual(empty.kind, 'purchase')

    def test_order_status_lock_cannot_be_bypassed_by_reopening_and_editing_at_once(self):
        order = self.create(status='approved')
        line = OrderLine.objects.create(order=order, product=self.main, quantity=2, unit_price=3)
        url = reverse('edit', args=['siparisler',order.pk])
        response = self.client.get(url)
        self.assertIsNone(response.context['formset'])
        self.assertContains(response, 'önce siparişi taslağa')
        response = self.client.post(url, self.header(number=order.number) |
            self.line_payload([{'id':line.pk,'order':order.pk,'product':self.main.pk,'quantity':9,'unit_price':3}],initial=1))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(reverse('edit', args=['siparis-kalemleri',line.pk]),
            {'order':order.pk,'product':self.main.pk,'quantity':9,'unit_price':3}).status_code, 403)
        order.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(order.status, 'approved')
        self.assertEqual(line.quantity, 2)
        self.assertEqual(self.client.post(url, self.header(number=order.number)).status_code, 302)
        self.assertIsNotNone(self.client.get(url).context['formset'])

    def test_draft_inline_changes_can_be_saved_and_approved_together(self):
        order = self.create()
        line = OrderLine.objects.create(order=order, product=self.main, quantity=1, unit_price=3)
        response = self.client.post(reverse('edit', args=['siparisler',order.pk]), self.header(number=order.number,status='approved') |
            self.line_payload([{'id':line.pk,'order':order.pk,'product':self.main.pk,'quantity':4,'unit_price':3}],initial=1))
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'approved')
        self.assertEqual(order.total, 12)

    def test_foreign_line_id_and_duplicate_line_id_are_rejected_atomically(self):
        own = self.create(number='OWN')
        other = self.create(number='OTHER')
        own_line = OrderLine.objects.create(order=own, product=self.main, quantity=1, unit_price=3)
        foreign = OrderLine.objects.create(order=other, product=self.main, quantity=5, unit_price=3)
        for ids, initial in [([foreign.pk],1),([own_line.pk,own_line.pk],1)]:
            with self.subTest(ids=ids):
                rows=[{'id':identity,'order':own.pk,'product':self.main.pk,'quantity':8,'unit_price':3} for identity in ids]
                response = self.client.post(reverse('edit', args=['siparisler',own.pk]),
                    self.header(number='OWN') | self.line_payload(rows,initial=initial))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['formset'].is_valid())
                self.assertEqual(own.lines.count(), 1)
                own_line.refresh_from_db()
                foreign.refresh_from_db()
                self.assertEqual(own_line.quantity, 1)
                self.assertEqual(foreign.quantity, 5)

    def test_missing_management_form_does_not_save_partial_data(self):
        response = self.client.post(reverse('create', args=['siparisler']), self.header() |
            {'lines-0-product':self.main.pk,'lines-0-quantity':1,'lines-0-unit_price':2})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['formset'].non_form_errors())
        self.assertEqual(Order.objects.count(), 0)

    def test_empty_draft_legacy_header_submission_remains_supported(self):
        response = self.client.post(reverse('create', args=['siparisler']), self.header())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.get().lines.count(), 0)
        response = self.client.post(reverse('create', args=['siparisler']), self.header(number='NO-LINES') | self.line_payload([]))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('create', args=['siparisler']))
        self.assertContains(response, 'data-add-line')

    def test_header_only_permission_cannot_add_or_expose_inline_lines(self):
        limited = User.objects.create_user('header-only')
        limited.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=['add_order','view_partner']))
        self.client.force_login(limited)
        url = reverse('create', args=['siparisler'])
        response = self.client.get(url)
        self.assertIsNone(response.context['formset'])
        self.assertNotContains(response, self.main.name)
        self.assertEqual(self.client.post(url, self.header() | self.line_payload([{'product':self.main.pk,'quantity':1,'unit_price':2}])).status_code, 403)
        self.assertEqual(Order.objects.count(), 0)

    def test_add_only_line_permission_cannot_edit_existing_inline_rows(self):
        limited = User.objects.create_user('add-only')
        limited.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=[
            'change_order','view_partner','view_orderline','add_orderline','view_product']))
        order = self.create()
        line = OrderLine.objects.create(order=order, product=self.main, quantity=1, unit_price=3)
        self.client.force_login(limited)
        response = self.client.post(reverse('edit', args=['siparisler',order.pk]), self.header(number=order.number) |
            self.line_payload([{'id':line.pk,'order':order.pk,'product':self.main.pk,'quantity':9,'unit_price':3}],initial=1))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['formset'].non_form_errors())
        line.refresh_from_db()
        self.assertEqual(line.quantity, 1)

    def test_options_endpoint_and_server_fallback_use_the_same_type_rules(self):
        url = reverse('order_product_options')
        sale = self.client.get(url, {'kind':'sale'}).json()
        purchase = self.client.get(url, {'kind':'purchase'}).json()
        self.assertEqual({p['id'] for p in sale['products']}, {self.main.pk,self.manual.pk})
        self.assertEqual({p['id'] for p in purchase['products']}, {self.raw.pk})
        self.assertEqual(set(sale['groups']), {'Banklar','Soba'})
        order = self.create('purchase')
        self.assertEqual(self.client.get(url, {'order':order.pk,'kind':'sale'}).json()['kind'], 'purchase')
        for params in ({'kind':'invalid'},{'order':'x'},{'order':'9'*100}):
            self.assertEqual(self.client.get(url,params).status_code, 400)
        self.assertEqual(self.client.post(url,{}).status_code, 405)
        response = self.client.post(reverse('create', args=['siparisler']), self.header(kind='purchase') |
            self.line_payload([{'product':self.main.pk,'quantity':'2.5','unit_price':10}]) | {'refresh_products':'1'})
        self.assertEqual(Order.objects.count(), 1)  # Only the existing purchase order.
        self.assertEqual(list(response.context['formset'].forms[0].fields['product'].queryset), [self.raw])
        self.assertEqual(response.context['formset'].forms[0]['quantity'].value(), '2.5')

    def test_group_filter_does_not_drop_a_selected_product_from_another_group(self):
        response = self.client.post(reverse('create', args=['siparisler']), self.header() |
            self.line_payload([{'product':self.main.pk,'quantity':1,'unit_price':2}]) |
            {'product_group':'Soba','refresh_products':'1'})
        choices = response.context['formset'].forms[0].fields['product'].queryset
        self.assertEqual(set(choices.values_list('pk',flat=True)), {self.main.pk,self.manual.pk})
        self.assertEqual(Order.objects.count(), 0)

    def test_options_and_line_selectors_enforce_permissions(self):
        user = User.objects.create_user('no-product-view')
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=['add_orderline','view_order']))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('order_product_options'), {'kind':'sale'}).status_code, 403)
        order = self.create()
        response = self.client.get(reverse('create', args=['siparis-kalemleri']), {'order':order.pk})
        self.assertFalse(response.context['form'].fields['product'].queryset.exists())
        self.assertNotContains(response, self.main.name)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('order_product_options'), {'kind':'sale'}).status_code, 302)

    def test_model_validation_rejects_wrong_types_outside_the_form(self):
        sale = self.create()
        purchase = self.create('purchase',number='BUY')
        for order, product in [(sale,self.raw),(purchase,self.main)]:
            with self.subTest(kind=order.kind):
                line = OrderLine(order=order,product=product,quantity=1,unit_price=1)
                with self.assertRaises(ValidationError):
                    line.full_clean()

    def test_linked_line_list_filters_exact_order_and_preserves_it_across_pages(self):
        order = self.create(number='FILTERED')
        other = self.create(number='OTHER')
        OrderLine.objects.bulk_create([OrderLine(order=order,product=self.main,quantity=1,unit_price=2) for _ in range(27)])
        other_line = OrderLine.objects.create(order=other,product=self.manual,quantity=1,unit_price=2)
        url = reverse('listing', args=['siparis-kalemleri'])
        response = self.client.get(url, {'order':order.pk})
        self.assertEqual(response.context['page'].paginator.count, 27)
        self.assertNotIn(other_line.pk, {row['pk'] for row in response.context['rows']})
        self.assertContains(response, f'order={order.pk}')
        self.assertContains(response, f'href="/modul/siparis-kalemleri/yeni/?order={order.pk}"')
        self.assertEqual(len(self.client.get(url, {'order':order.pk,'page':2}).context['rows']), 2)
        order.status='approved'
        order.save(update_fields=['status'])
        self.assertTrue(all(not row['editable'] for row in self.client.get(url, {'order':order.pk}).context['rows']))
