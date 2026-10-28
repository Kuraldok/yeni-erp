from decimal import Decimal
from io import BytesIO
from urllib.parse import urlencode

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from .catalog import import_catalog
from .models import CatalogImport, MaterialRecipe, MaterialRequirement, Product


def catalog_file(quantity=2):
    book = Workbook()
    book.remove(book.active)
    for name, rows in [
        ('Banklar', [
            [1, 'P-1', 'Ana Bank', 'S-1', 'Ahşap yarı mamul', 'Yarı Mamul', quantity, 'Adet'],
            [2, 'P-1', 'Ana Bank', 'R-1', 'Pik hammadde', 'Hammadde', None, 'KG'],
            [3, 'P-1', 'Ana Bank', 'F-1', 'Bitmiş bileşen', 'Mamul', 1, 'Adet'],
        ]),
        ('Masalar', [[1, 'P-2', 'Ana Masa', 'S-2', 'Masa yarı mamulü', 'Yarı Mamul', 3, 'Adet']]),
    ]:
        sheet = book.create_sheet(name)
        sheet.append(['', 'Ürün ve Ara Mamul Listesi'])
        sheet.append([])
        sheet.append(['Sıra No', 'Üst Ürün Kodu', 'Üst Ürün Adı', 'Bileşen Kodu', 'Bileşen Adı', 'Bileşen Türü', 'Birim İhtiyacı', 'Ölçü Birimi'])
        for row in rows:
            sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class ProductScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('product-admin', password='Only-For-Tests-5128')
        cls.batch, _ = import_catalog(catalog_file(), 'test.xlsx')
        cls.parent = Product.objects.get(code='P-1')
        cls.other = Product.objects.get(code='P-2')
        cls.manual = Product.objects.create(code='M-1', name='Yeni ana ürün', kind='finished', category='Özel Ürünler')
        cls.ungrouped = Product.objects.create(code='M-2', name='Reçetesiz ana ürün')
        cls.raw = Product.objects.create(code='R-2', name='Bağımsız hammadde', kind='raw')
        cls.semi = Product.objects.create(code='S-3', name='Bağımsız yarı mamul', kind='semi')
        cls.url = reverse('listing', args=['urunler'])

    def setUp(self):
        self.client.force_login(self.admin)

    def ids(self, response):
        return {row['pk'] for row in response.context['rows']}

    def test_main_list_excludes_components_including_finished_component(self):
        response = self.client.get(self.url)
        self.assertEqual(self.ids(response), {self.parent.pk, self.other.pk, self.manual.pk, self.ungrouped.pk})
        for name in ['Ahşap yarı mamul', 'Pik hammadde', 'Bitmiş bileşen', 'Bağımsız hammadde', 'Bağımsız yarı mamul']:
            self.assertNotContains(response, name)
        self.assertContains(response, '4 ana ürün')

    def test_semifinished_with_own_recipe_still_not_in_main_list(self):
        MaterialRecipe.objects.create(source=self.batch, sheet='Banklar', parent_code=self.semi.code, parent_name=self.semi.name, product=self.semi)
        self.assertNotIn(self.semi.pk, self.ids(self.client.get(self.url)))

    def test_category_and_search_work_together(self):
        response = self.client.get(self.url, {'category': 'Banklar', 'q': 'Ana'})
        self.assertEqual(self.ids(response), {self.parent.pk})
        self.assertContains(response, 'value="Banklar" selected', html=False)
        self.assertEqual(self.ids(self.client.get(self.url, {'category': 'Masalar'})), {self.other.pk})
        self.assertEqual(self.ids(self.client.get(self.url, {'category': 'Özel Ürünler'})), {self.manual.pk})
        self.assertEqual(self.ids(self.client.get(self.url, {'category': 'Banklar', 'q': 'Masa'})), set())
        self.assertContains(self.client.get(self.url, {'category': 'bilinmeyen-grup'}), 'Filtrelerle eşleşen kayıt bulunamadı.')

    def test_unclassified_category_filter(self):
        response = self.client.get(self.url, {'category': '__none__'})
        self.assertEqual(self.ids(response), {self.ungrouped.pk})

    def test_source_group_used_when_card_group_blank(self):
        self.parent.category = ''
        self.parent.save(update_fields=['category'])
        response = self.client.get(self.url, {'category': 'Banklar'})
        self.assertEqual(self.ids(response), {self.parent.pk})
        self.assertIn('Banklar', response.context['groups'])
        response = self.client.get(reverse('product_detail', args=[self.parent.pk]), {'tab': 'overview'})
        self.assertEqual(response.context['display_group'], 'Banklar')

    def test_filter_and_search_survive_pagination(self):
        Product.objects.bulk_create([Product(code=f'M-NEW-{i:02}', name='Özel ana ürün', kind='finished', category='Özel Ürünler') for i in range(27)])
        response = self.client.get(self.url, {'category': 'Özel Ürünler', 'q': 'Özel ana'})
        self.assertEqual(response.context['page'].paginator.count, 27)
        self.assertEqual(len(response.context['rows']), 25)
        self.assertContains(response, urlencode({'category': 'Özel Ürünler'}).replace('+', '%20'))
        response = self.client.get(self.url, {'category': 'Özel Ürünler', 'q': 'Özel ana', 'page': 2})
        self.assertEqual(len(response.context['rows']), 2)
        self.assertEqual(response.context['category'], 'Özel Ürünler')

    def test_product_code_and_name_open_separate_tab(self):
        response = self.client.get(self.url, {'category': 'Banklar'})
        link = reverse('product_detail', args=[self.parent.pk])
        self.assertContains(response, f'href="{link}" target="_blank" rel="noopener"')
        self.assertEqual(response.context['rows'][0]['product_detail'], link)

    def test_detail_groups_components_and_preserves_blanks(self):
        response = self.client.get(reverse('product_detail', args=[self.parent.pk]))
        self.assertEqual(response.context['tab'], 'components')
        sections = response.context['sections']
        self.assertEqual([s['title'] for s in sections], ['Yarı mamuller', 'Hammaddeler', 'Diğer bileşenler'])
        self.assertEqual(sections[0]['rows'][0]['line'].component_code, 'S-1')
        self.assertEqual(sections[0]['rows'][0]['line'].quantity, Decimal('2'))
        self.assertEqual(sections[1]['rows'][0]['line'].component_code, 'R-1')
        self.assertIsNone(sections[1]['rows'][0]['line'].quantity)
        self.assertContains(response, 'Birim İhtiyacı boş.')
        self.assertNotContains(response, 'Masa yarı mamulü')
        line = MaterialRequirement.objects.get(component_code='R-1')
        self.assertIsNone(line.quantity)
        self.assertIsNone(line.raw['Birim İhtiyacı'])
        overview = self.client.get(reverse('product_detail', args=[self.parent.pk]), {'tab': 'overview'})
        self.assertContains(overview, '<dt>Ürün kodu</dt>', html=True)

    def test_recipe_versions_are_selected_not_merged(self):
        newer, _ = import_catalog(catalog_file(5), 'new.xlsx')
        url = reverse('product_detail', args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.context['selected_recipe'].source_id, newer.pk)
        self.assertEqual(response.context['sections'][0]['rows'][0]['line'].quantity, 5)
        older = MaterialRecipe.objects.get(source=self.batch, product=self.parent)
        response = self.client.get(url, {'recipe': older.pk})
        self.assertEqual(response.context['selected_recipe'].source_id, self.batch.pk)
        self.assertEqual(response.context['sections'][0]['rows'][0]['line'].quantity, 2)
        self.assertEqual(self.client.get(self.url).context['page'].paginator.count, 4)

    def test_cannot_request_another_products_recipe_or_component_as_main(self):
        other_recipe = MaterialRecipe.objects.get(product=self.other)
        url = reverse('product_detail', args=[self.parent.pk])
        self.assertEqual(self.client.get(url, {'recipe': other_recipe.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {'recipe': 'bad'}).status_code, 404)
        self.assertEqual(self.client.get(url, {'tab': 'bad'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('product_detail', args=[self.raw.pk])).status_code, 404)

    def test_component_permissions_and_login_required(self):
        user = User.objects.create_user('only-product-view')
        user.user_permissions.add(Permission.objects.get(content_type__app_label='erp', codename='view_product'))
        self.client.force_login(user)
        url = reverse('product_detail', args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['tab'], 'overview')
        self.assertNotContains(response, 'Ahşap yarı mamul')
        self.assertEqual(self.client.get(url, {'tab': 'components'}).status_code, 403)
        no_role = User.objects.create_user('no-product-role')
        self.client.force_login(no_role)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_manual_product_without_recipe_has_clear_empty_state(self):
        self.assertContains(self.client.get(reverse('product_detail', args=[self.manual.pk])), 'Bu ana ürün için henüz reçete kaydı yok.')

    def test_brand_label_and_logo(self):
        response = self.client.get(self.url)
        self.assertContains(response, '<span class="logo">K</span> Kural Döküm', html=False)
        self.assertNotContains(response, 'Yeni ERP')
        self.client.logout()
        self.assertContains(self.client.get(reverse('login')), 'Kural Döküm')
