from datetime import date

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .catalog import import_catalog
from .models import MaterialRecipe, MaterialRequirement, Order, OrderLine, Partner, Product, StockMovement, Warehouse
from .stock import reserve_order_line
from .test_products import catalog_file


class StockCardScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('stock-card-admin', password='Fixture-only-stock-cards-5128')
        cls.batch, _ = import_catalog(catalog_file(), 'stock-cards.xlsx')
        cls.parent = Product.objects.get(code='P-1')
        cls.parent.unit = 'adet'
        cls.parent.save(update_fields=['unit'])
        cls.other = Product.objects.get(code='P-2')
        cls.raw = Product.objects.get(code='R-1')
        cls.semi = Product.objects.get(code='S-1')
        cls.manual = Product.objects.create(code='M-1', name='Elle açılmış ana ürün', kind='finished', category='Özel')
        cls.part = Product.objects.create(code='PART-1', name='Bağlantı parçası', kind='part', unit='adet', category='Banklar')
        recipe = MaterialRecipe.objects.get(source=cls.batch, product=cls.parent)
        MaterialRequirement.objects.create(source=cls.batch, recipe=recipe, sheet='Banklar', row_number=999,
            parent_code=cls.parent.code, component=cls.part, component_code=cls.part.code,
            component_name=cls.part.name, component_kind='Parça', quantity=1, unit='Adet')
        cls.warehouse = Warehouse.objects.create(name='Kart test deposu')
        StockMovement.objects.create(product=cls.parent, warehouse=cls.warehouse, kind='in', quantity=10,
            unit_cost=3, note='Sentetik mamul açılışı', created_by=cls.admin)
        StockMovement.objects.create(product=cls.raw, warehouse=cls.warehouse, kind='in', quantity=20,
            unit_cost=4, note='Sentetik hammadde açılışı', created_by=cls.admin)
        partner = Partner.objects.create(name='Kart test müşterisi', kind='customer')
        order = Order.objects.create(number='CARD-ORDER', kind='sale', partner=partner, date=date(2026,11,2))
        line = OrderLine.objects.create(order=order, product=cls.parent, quantity=4, unit_price=7)
        order.status = 'approved'
        order.save()
        reserve_order_line(line.pk, cls.warehouse.pk, 2, cls.admin)
        cls.url = reverse('listing', args=['stok-kartlari'])

    def setUp(self):
        self.client.force_login(self.admin)

    def test_stock_list_matches_main_products_without_rewriting_components(self):
        before = list(MaterialRequirement.objects.order_by('pk').values())
        MaterialRecipe.objects.create(source=self.batch, sheet='Banklar', parent_code=self.semi.code, product=self.semi)
        products = self.client.get(reverse('listing', args=['urunler']))
        response = self.client.get(self.url)
        self.assertEqual([r['pk'] for r in response.context['rows']], [r['pk'] for r in products.context['rows']])
        self.assertContains(response, '3 ana ürün')
        for code in ('R-1', 'S-1', 'F-1', 'S-2', 'PART-1'):
            self.assertNotContains(response, code)
        self.assertEqual(list(MaterialRequirement.objects.order_by('pk').values()), before)
        self.assertEqual(self.client.get(self.url, {'q': self.raw.code}).context['page'].paginator.count, 0)

    def test_search_group_order_and_pagination_match_product_cards(self):
        self.parent.category = ''
        self.parent.save(update_fields=['category'])
        Product.objects.bulk_create([Product(code=f'A-{i:02}', name='Liste ana ürünü', kind='finished', category='Banklar') for i in range(27)])
        for query in ({'category': 'Banklar'}, {'category': 'Banklar', 'page': 2}, {'category': 'Banklar', 'q': 'Liste', 'page': 2}):
            stock = self.client.get(self.url, query)
            products = self.client.get(reverse('listing', args=['urunler']), query)
            self.assertEqual([r['pk'] for r in stock.context['rows']], [r['pk'] for r in products.context['rows']])
            self.assertEqual(stock.context['page'].paginator.count, products.context['page'].paginator.count)
            self.assertContains(stock, 'value="Banklar" selected')
        first = self.client.get(self.url, {'category': 'Banklar'})
        self.assertEqual(first.context['rows'][0]['values'][0], 'A-00')
        self.assertContains(first, 'category=Banklar')
        self.assertEqual(self.client.get(self.url, {'category': 'Masalar'}).context['page'].paginator.count, 1)

    def test_stock_links_and_shared_details_stay_inside_the_stock_screen(self):
        url = reverse('stock_card_detail', args=[self.parent.pk])
        listing = self.client.get(self.url, {'category': 'Banklar'})
        self.assertContains(listing, f'href="{url}" data-product-open', count=3)
        self.assertContains(listing, 'Stok listesi')
        self.assertNotContains(listing, 'target="_blank"')
        self.assertContains(listing, '/static/product-tabs.js')
        for headers in ({}, {'X-Product-Tab': '1'}):
            response = self.client.get(url, headers=headers)
            self.assertContains(response, 'Ahşap yarı mamul')
            self.assertContains(response, 'Pik hammadde')
            self.assertContains(response, 'Bağlantı parçası')
            self.assertContains(response, f'href="{url}?tab=stock')
            self.assertContains(response, f'action="{url}"')
            self.assertContains(response, f'href="{self.url}" data-product-list')
            self.assertContains(response, reverse('edit', args=['stok-kartlari', self.parent.pk]))
            self.assertContains(response, f'href="{reverse("stock_card_detail", args=[self.raw.pk])}" data-product-open')
            self.assertNotContains(response, 'Kaynak satırı')
            self.assertIn('X-Product-Tab', response['Vary'])
            if headers:
                self.assertTemplateNotUsed(response, 'base.html')

    def test_recipe_versions_and_invalid_requests_are_validated_in_stock_details(self):
        newer, _ = import_catalog(catalog_file(5), 'stock-new.xlsx')
        older = MaterialRecipe.objects.get(source=self.batch, product=self.parent)
        other = MaterialRecipe.objects.get(source=newer, product=self.other)
        url = reverse('stock_card_detail', args=[self.parent.pk])
        for headers in ({}, {'X-Product-Tab': '1'}):
            response = self.client.get(url, headers=headers)
            self.assertEqual(response.context['selected_recipe'].source_id, newer.pk)
            self.assertEqual(response.context['sections'][0]['rows'][0]['line'].quantity, 5)
            response = self.client.get(url, {'recipe': older.pk}, headers=headers)
            self.assertEqual(response.context['sections'][0]['rows'][0]['line'].quantity, 2)
            for query in ({'recipe': other.pk}, {'recipe': 'bad'}, {'tab': 'bad'}):
                self.assertEqual(self.client.get(url, query, headers=headers).status_code, 404)

    def test_stock_balances_and_reservations_keep_their_existing_values(self):
        url = reverse('stock_card_detail', args=[self.parent.pk])
        before = list(StockMovement.objects.order_by('pk').values())
        for headers in ({}, {'X-Product-Tab': '1'}):
            response = self.client.get(url, {'tab': 'stock'}, headers=headers)
            row = response.context['stock_rows'][0]
            self.assertEqual((row['quantity'], row['reserved'], row['available']), (10, 2, 8))
            self.assertEqual((row['valuation']['average'], row['valuation']['value']), (3, 30))
            self.assertContains(response, 'Depo bakiyeleri')
            self.assertContains(response, self.warehouse.name)
        self.assertEqual(list(StockMovement.objects.order_by('pk').values()), before)

    def test_component_cards_and_operational_product_selectors_remain_accessible(self):
        for product in (self.raw, self.semi, self.part):
            url = reverse('stock_card_detail', args=[product.pk])
            for headers in ({}, {'X-Product-Tab': '1'}):
                response = self.client.get(url, headers=headers)
                self.assertContains(response, 'Depo bakiyeleri')
                self.assertContains(response, f'data-product-id="{product.pk}"')
                if headers:
                    self.assertTemplateNotUsed(response, 'base.html')
            self.assertEqual(self.client.get(reverse('edit', args=['stok-kartlari', product.pk])).status_code, 200)
        form = self.client.get(reverse('create', args=['stok'])).context['form']
        choices = set(form.fields['product'].queryset.values_list('pk', flat=True))
        self.assertTrue({self.raw.pk, self.part.pk} <= choices)
        self.assertNotIn(self.semi.pk, choices)

    def test_stock_details_preserve_component_stock_and_reservation_permissions(self):
        viewer = User.objects.create_user('stock-card-viewer')
        viewer.user_permissions.add(Permission.objects.get(content_type__app_label='erp', codename='view_product'))
        self.client.force_login(viewer)
        url = reverse('stock_card_detail', args=[self.parent.pk])
        for headers in ({}, {'X-Product-Tab': '1'}):
            response = self.client.get(url, headers=headers)
            self.assertEqual(response.context['tab'], 'overview')
            self.assertNotContains(response, 'Pik hammadde')
            self.assertNotContains(response, self.warehouse.name)
            for tab in ('components', 'stock'):
                self.assertEqual(self.client.get(url, {'tab': tab}, headers=headers).status_code, 403)
            raw = self.client.get(reverse('stock_card_detail', args=[self.raw.pk]), headers=headers)
            self.assertNotContains(raw, self.warehouse.name)
        viewer.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=['view_stockmovement', 'view_warehouse']))
        for headers in ({}, {'X-Product-Tab': '1'}):
            response = self.client.get(url, {'tab': 'stock'}, headers=headers)
            row = response.context['stock_rows'][0]
            self.assertEqual(row['quantity'], 10)
            self.assertIsNone(row['reserved'])
            self.assertIsNone(row['available'])
        no_role = User.objects.create_user('stock-card-no-role')
        self.client.force_login(no_role)
        self.assertEqual(self.client.get(url, headers={'X-Product-Tab': '1'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url, headers={'X-Product-Tab': '1'}).status_code, 302)
