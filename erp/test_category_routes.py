from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .catalog import import_catalog
from .models import (
    MaterialRequirement, Product, ProductionJob, ProductionRoute, RouteStep,
    StockMovement,
)
from .production import advance_job
from .routing import apply_component_routes
from .test_routing import workbook


class CategoryRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('category-admin', password='Category-Tests-7819')

    def route(self, code):
        return ProductionRoute.objects.select_related('product', 'flow').get(product__code=code)

    def test_specified_casting_categories_accept_both_component_labels(self):
        categories = (
            'Şömineler', 'Posta kutusu', 'Soba', 'Sandalye', 'Kapak',
            'Bariyer', 'Ferforje korkuluk', 'Masa', 'Masa ayakları',
            'Masa ve masa ayakları', 'Döküm saksı',
        )
        sheets = []
        for index, category in enumerate(categories):
            sheets.append((category, [
                [1, f'P-{index}', 'Ana ürün', f'S-{index}', 'Gövde', 'Yarı Mamul', 2, 'Adet'],
                [2, f'P-{index}', 'Ana ürün', f'F-{index}', 'Ayak', 'Mamul', 1, 'Adet'],
            ]))
        batch, _ = import_catalog(workbook(sheets), 'categories.xlsx')
        self.assertEqual(batch.summary['conflict_codes'], [])
        self.assertEqual(ProductionRoute.objects.count(), len(categories) * 2)
        for index, category in enumerate(categories):
            with self.subTest(category=category):
                for prefix, kind in [('S', 'semi'), ('F', 'finished')]:
                    route = self.route(f'{prefix}-{index}')
                    self.assertEqual(route.flow.code, 'casting')
                    self.assertEqual(route.supply_method, 'manufacture')
                    self.assertEqual(route.product.kind, kind)
                    self.assertEqual(route.product.production_area, 'casting')
                    self.assertEqual(route.steps.count(), 7)
                self.assertFalse(ProductionRoute.objects.filter(product__code=f'P-{index}').exists())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_mangal_wood_exceptions_and_other_components(self):
        cases = [
            ('Ahşap saplı kulp', 'Yarı Mamul', 'wood'),
            ('Ahşap saplı kulp', 'Mamul', 'wood'),
            ('Tel ızgara sapı', 'Yarı Mamul', 'wood'),
            ('  TEL   IZGARA SAPI  ', 'Mamul', 'wood'),
            ('Gövde', 'Yarı Mamul', 'casting'),
            ('Tel ızgara', 'Mamul', 'casting'),
        ]
        rows = [[i + 1, 'P', 'Mangal', f'C-{i}', name, kind, 1, 'Adet']
                for i, (name, kind, _) in enumerate(cases)]
        import_catalog(workbook([('Mangallar', rows)]), 'mangal.xlsx')
        for i, (name, _, area) in enumerate(cases):
            with self.subTest(component=name):
                route = self.route(f'C-{i}')
                self.assertEqual(route.flow.code, area)
                self.assertEqual(route.product.production_area, area)
                self.assertEqual(route.steps.count(), 4 if area == 'wood' else 7)

    def test_wood_planters_accept_both_labels_and_preserve_unknown_amounts(self):
        import_catalog(workbook([('Ahşap saksı', [
            [1, 'P', 'Saksı', 'S', 'Yan bölüm', 'Yarı Mamul', None, 'Adet'],
            [2, 'P', 'Saksı', 'F', 'Taban', 'Mamul', 1, 'Adet'],
        ])]), 'wood.xlsx')
        for code in ('S', 'F'):
            self.assertEqual(self.route(code).flow.code, 'wood')
        line = MaterialRequirement.objects.get(component_code='S')
        self.assertIsNone(line.quantity)
        self.assertIsNone(line.raw['Birim İhtiyacı'])
        self.assertFalse(RouteStep.objects.exclude(duration_minutes=None).exists())

    def test_category_aliases_case_and_whitespace(self):
        cases = [
            ('Şömine', 'casting'),
            ('Posta kutuları', 'casting'), ('Sobalar', 'casting'),
            ('Sandalyeler', 'casting'), ('Kapaklar', 'casting'),
            ('Bariyerler', 'casting'), ('Ferforje korkuluklar', 'casting'),
            ('Masalar', 'casting'), ('Masa ayağı', 'casting'),
            ('Masalar ve masa ayakları', 'casting'),
            ('Döküm saksılar', 'casting'), ('Döküm saksıları', 'casting'),
            ('Ahşap saksılar', 'wood'), ('Ahşap saksıları', 'wood'),
            ('  DÖKÜM   SAKSI  ', 'casting'),
        ]
        sheets = [(category, [[1, f'P-{i}', 'Ürün', f'C-{i}', 'Bölüm', 'Mamul', 1, 'Adet']])
                  for i, (category, _) in enumerate(cases)]
        import_catalog(workbook(sheets), 'aliases.xlsx')
        for i, (category, area) in enumerate(cases):
            with self.subTest(category=category):
                self.assertEqual(self.route(f'C-{i}').flow.code, area)

    def test_mamul_component_is_shown_with_semifinished_without_retyping_source(self):
        import_catalog(workbook([('Mangal', [
            [1, 'P', 'Mangal', 'F', 'Ahşap saplı kulp', 'Mamul', 2, 'Adet'],
            [2, 'P', 'Mangal', 'R', 'Pik', 'Hammadde', 3, 'KG'],
            [3, 'P', 'Mangal', 'X', 'Vida', 'Parça', 4, 'Adet'],
        ])]), 'display.xlsx')
        self.client.force_login(self.user)
        response = self.client.get(reverse('product_detail', args=[Product.objects.get(code='P').pk]))
        self.assertEqual(response.status_code, 200)
        sections = response.context['sections']
        self.assertEqual([r['line'].component_code for r in sections[0]['rows']], ['F'])
        self.assertEqual([r['line'].component_code for r in sections[1]['rows']], ['R'])
        self.assertEqual([r['line'].component_code for r in sections[2]['rows']], ['X'])
        route = self.route('F')
        self.assertContains(response, reverse('route_detail', args=[route.pk]))
        self.assertContains(response, '<td>Mamul</td>')
        line = MaterialRequirement.objects.get(component_code='F')
        self.assertEqual(line.component_kind, 'Mamul')
        self.assertEqual(line.raw['Bileşen Türü'], 'Mamul')
        self.assertEqual(line.component.kind, 'finished')
        self.assertFalse(ProductionRoute.objects.filter(product__code__in=['P', 'R', 'X']).exists())
        self.assertContains(self.client.get(reverse('route_detail', args=[route.pk])), 'Lata üretimi')

    def test_mixed_mamul_and_semifinished_labels_match_one_card_and_route(self):
        batch, _ = import_catalog(workbook([('Mangal', [
            [1, 'P', 'Mangal', 'C', 'Gövde', 'Mamul', 1, 'Adet'],
            [2, 'P', 'Mangal', 'C', 'Gövde', 'Yarı Mamul', 2, 'Adet'],
        ])]), 'mixed.xlsx')
        self.assertEqual(batch.summary['conflict_codes'], [])
        self.assertEqual(Product.objects.filter(code='C').count(), 1)
        self.assertEqual(ProductionRoute.objects.count(), 1)
        self.assertEqual(self.route('C').flow.code, 'casting')
        lines = list(MaterialRequirement.objects.filter(component_code='C').order_by('row_number'))
        self.assertEqual([line.component_kind for line in lines], ['Mamul', 'Yarı Mamul'])
        self.assertEqual([line.raw['Bileşen Türü'] for line in lines], ['Mamul', 'Yarı Mamul'])
        self.assertTrue(all(line.component_id == self.route('C').product_id for line in lines))
        self.assertTrue(all(not line.issues for line in lines))

    def test_normalized_component_labels_keep_original_source_values(self):
        batch, _ = import_catalog(workbook([('Soba', [
            [1, 'P', 'Soba', 'C', 'Gövde', 'MAMUL', 1, 'Adet'],
            [2, 'P', 'Soba', 'C', 'Gövde', 'YARI   MAMUL', 2, 'Adet'],
        ])]), 'labels.xlsx')
        self.assertEqual(batch.summary['conflict_codes'], [])
        self.assertEqual(self.route('C').flow.code, 'casting')
        self.assertEqual(list(MaterialRequirement.objects.values_list('component_kind', flat=True)), ['MAMUL', 'YARI   MAMUL'])
        self.assertTrue(all(not line.issues for line in MaterialRequirement.objects.all()))

    def test_new_version_with_equivalent_label_keeps_existing_card_type_and_route(self):
        for i, (first, second, kind) in enumerate([
            ('Mamul', 'Yarı Mamul', 'finished'), ('Yarı Mamul', 'Mamul', 'semi'),
        ]):
            with self.subTest(original=first):
                code = f'C-{i}'
                row = [1, f'P-{i}', 'Soba', code, 'Gövde', first, 1, 'Adet']
                import_catalog(workbook([('Soba', [row])]), 'first.xlsx')
                product = Product.objects.get(code=code)
                product.minimum_stock = Decimal('17')
                product.save(update_fields=['minimum_stock'])
                route = self.route(code)
                row[5] = second
                batch, _ = import_catalog(workbook([('Soba', [row])]), 'second.xlsx')
                product.refresh_from_db()
                self.assertEqual(product.kind, kind)
                self.assertEqual(product.minimum_stock, 17)
                self.assertEqual(batch.summary['conflict_codes'], [])
                self.assertEqual(self.route(code).pk, route.pk)
                self.assertEqual(MaterialRequirement.objects.get(source=batch).component_id, product.pk)

    def test_real_name_unit_and_type_conflicts_still_block_matching(self):
        for i, (category, changes) in enumerate([
            ('Soba', {4: 'Kapak'}), ('Soba', {7: 'KG'}),
            ('Soba', {5: 'Hammadde'}), ('Diğer ürünler', {5: 'Yarı Mamul'}),
        ]):
            with self.subTest(category=category, changes=changes):
                code = f'C-{i}'
                first = [1, f'P-{i}', 'Ürün', code, 'Gövde', 'Mamul', 1, 'Adet']
                second = first.copy()
                second[0] = 2
                for index, value in changes.items():
                    second[index] = value
                batch, _ = import_catalog(workbook([(category, [first, second])]), 'conflict.xlsx')
                self.assertIn(code, batch.summary['conflict_codes'])
                self.assertFalse(Product.objects.filter(code=code).exists())
                self.assertTrue(all(line.component_id is None for line in MaterialRequirement.objects.filter(source=batch)))

    def test_unlisted_categories_other_kinds_and_legacy_mamul_are_unchanged(self):
        import_catalog(workbook([
            ('Diğer ürünler', [
                [1, 'P1', 'Ürün', 'S', 'Gövde', 'Yarı Mamul', 1, 'Adet'],
                [2, 'P1', 'Ürün', 'F', 'Ayak', 'Mamul', 1, 'Adet'],
            ]),
            ('Soba', [
                [1, 'P2', 'Soba', 'R', 'Pik', 'Hammadde', 1, 'KG'],
                [2, 'P2', 'Soba', 'X', 'Vida', 'Parça', 1, 'Adet'],
            ]),
            ('Banklar', [[1, 'P3', 'Bank', 'B', 'Bitmiş bileşen', 'Mamul', 1, 'Adet']]),
        ]), 'unaffected.xlsx')
        self.assertEqual(ProductionRoute.objects.count(), 0)
        self.assertFalse(Product.objects.exclude(production_area='').exists())
        self.assertFalse(Product.objects.exclude(supply_method='').exists())

    def test_self_component_is_not_assigned_a_route(self):
        import_catalog(workbook([('Soba', [
            [1, 'P', 'Soba', 'P', 'Soba', 'Mamul', 1, 'Adet'],
        ])]), 'self.xlsx')
        self.assertEqual(ProductionRoute.objects.count(), 0)
        product = Product.objects.get(code='P')
        self.assertEqual(product.production_area, '')
        self.assertEqual(product.supply_method, '')
        self.assertTrue(MaterialRequirement.objects.get().issues)

    def test_blank_mangal_names_and_unlinked_cards_are_reported_without_guessing(self):
        import_catalog(workbook([('Mangal', [
            [1, 'P', 'Mangal', 'C', None, 'Mamul', 1, 'Adet'],
            [2, 'P', 'Mangal', None, 'Gövde', 'Yarı Mamul', 1, 'Adet'],
        ])]), 'missing.xlsx')
        result = apply_component_routes()
        self.assertEqual(ProductionRoute.objects.count(), 0)
        self.assertTrue(any('adı boş' in issue for issue in result['skipped']))
        self.assertTrue(any('eşleştirilmemiş' in issue for issue in result['skipped']))

    def test_cross_category_area_conflict_does_not_assign_a_route(self):
        import_catalog(workbook([
            ('Döküm saksı', [[1, 'P1', 'Döküm saksı', 'C', 'Gövde', 'Mamul', 1, 'Adet']]),
            ('Ahşap saksı', [[1, 'P2', 'Ahşap saksı', 'C', 'Gövde', 'Yarı Mamul', 1, 'Adet']]),
        ]), 'area-conflict.xlsx')
        product = Product.objects.get(code='C')
        self.assertEqual(product.production_area, '')
        self.assertEqual(product.supply_method, '')
        self.assertFalse(ProductionRoute.objects.exists())
        self.assertTrue(any('farklı rota kuralları' in issue for issue in apply_component_routes()['skipped']))

    def test_existing_routes_custom_steps_and_times_survive_repeated_setup(self):
        product = Product.objects.create(code='C', name='Gövde', kind='finished', unit='adet')
        manual = ProductionRoute.objects.create(product=product, name='Özel rota', version=1)
        manual_step = RouteStep.objects.create(route=manual, sequence=1, name='Özel işlem', workstation='Atölye', duration_minutes=19)
        content = workbook([('Soba', [[1, 'P', 'Soba', 'C', 'Gövde', 'Mamul', 1, 'Adet']])])
        batch, _ = import_catalog(content, 'preserve.xlsx')
        managed = ProductionRoute.objects.get(product=product, rule_key='fountain-bench-v1')
        self.assertEqual(managed.version, 2)
        step = managed.steps.get(sequence=2)
        step.name = 'Özel döküm'
        step.duration_minutes = 23
        step.save(update_fields=['name', 'duration_minutes'])
        for _ in range(2):
            self.assertEqual(apply_component_routes()['created'], 0)
        repeated, created = import_catalog(content, 'renamed.xlsx')
        self.assertFalse(created)
        self.assertEqual(repeated.pk, batch.pk)
        self.assertEqual(ProductionRoute.objects.count(), 2)
        manual.refresh_from_db()
        manual_step.refresh_from_db()
        step.refresh_from_db()
        self.assertEqual(manual.name, 'Özel rota')
        self.assertEqual(manual_step.duration_minutes, 19)
        self.assertEqual(step.name, 'Özel döküm')
        self.assertEqual(step.duration_minutes, 23)

    def test_later_conflicting_category_keeps_existing_assignment(self):
        import_catalog(workbook([('Ahşap saksı', [
            [1, 'P1', 'Ahşap saksı', 'C', 'Gövde', 'Mamul', 1, 'Adet'],
        ])]), 'first.xlsx')
        original = self.route('C')
        import_catalog(workbook([('Döküm saksı', [
            [1, 'P2', 'Döküm saksı', 'C', 'Gövde', 'Yarı Mamul', 1, 'Adet'],
        ])]), 'conflict.xlsx')
        product = Product.objects.get(code='C')
        self.assertEqual(product.kind, 'finished')
        self.assertEqual(product.production_area, 'wood')
        self.assertEqual(self.route('C').pk, original.pk)
        self.assertEqual(self.route('C').flow.code, 'wood')
        self.assertTrue(apply_component_routes()['skipped'])

    def test_generated_routes_start_jobs_with_correct_steps_without_stock_changes(self):
        import_catalog(workbook([
            ('Mangal', [[1, 'P1', 'Mangal', 'W', 'Tel ızgara sapı', 'Mamul', 1, 'Adet']]),
            ('Soba', [[1, 'P2', 'Soba', 'C', 'Gövde', 'Mamul', 1, 'Adet']]),
        ]), 'jobs.xlsx')
        for code, count in [('W', 4), ('C', 7)]:
            route = self.route(code)
            job = ProductionJob.objects.create(stock_mode='tracking',number=f'IE-{code}', product=route.product, quantity=2, unit='adet', flow=route.flow, created_by=self.user)
            if code == 'C':
                with self.assertRaises(ValidationError):
                    advance_job(job.pk, self.user, 'start')
                job.casting_material = 'pig'
                job.save(update_fields=['casting_material'])
            advance_job(job.pk, self.user, 'start')
            job.refresh_from_db()
            self.assertEqual(job.route_id, route.pk)
            self.assertEqual(job.steps.count(), count)
            self.assertEqual(list(job.steps.values_list('name', flat=True)), list(route.steps.values_list('name', flat=True)))
        self.assertEqual(StockMovement.objects.count(), 0)
