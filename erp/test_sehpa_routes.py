from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .catalog import import_catalog
from .models import MaterialRequirement,Product,ProductionRoute
from .routing import apply_component_routes
from .test_routing import workbook


class SehpaRoutingTests(TestCase):
    def route(self,code):return ProductionRoute.objects.select_related('flow','product').get(product__code=code)

    def test_tabletops_are_wood_and_other_semifinished_are_casting(self):
        import_catalog(workbook([('Sehpalar',[
            [1,'S-P','Sehpa','S-TOP','Masa tablası','Yarı Mamul',1,'Adet'],
            [2,'S-P','Sehpa','S-LEG','Ayak','Yarı Mamul',4,'Adet'],
            [3,'S-P','Sehpa','S-BODY','Gövde','Yarı Mamul',1,'Adet'],
        ])]),'sehpa.xlsx')
        top=self.route('S-TOP')
        self.assertEqual(top.flow.code,'wood')
        self.assertEqual(list(top.steps.values_list('name',flat=True)),['Ahşap hammadde','Lata üretimi','Boya','Hazır ahşap parçalar'])
        for code in ('S-LEG','S-BODY'):
            route=self.route(code)
            self.assertEqual(route.flow.code,'casting')
            self.assertEqual(route.product.production_area,'casting')
            self.assertEqual(route.steps.count(),7)

    def test_category_name_and_tabletop_name_allow_case_and_spaces(self):
        for index,category in enumerate(('Sehpa','Sehpalar',' SEHPALAR ')):
            import_catalog(workbook([(category,[[1,f'P{index}','Sehpa',f'T{index}','  MASA   TABLASI  ','Yarı Mamul',1,'Adet']])]),f'normal-{index}.xlsx')
            self.assertEqual(self.route(f'T{index}').flow.code,'wood')

    def test_mamul_labels_are_supported_without_rewriting_source_or_card_kind(self):
        batch,_=import_catalog(workbook([('Sehpalar',[
            [1,'P','Sehpa','TOP','Masa tablası','Mamul',1,'Adet'],
            [2,'P','Sehpa','LEG','Ayak','Mamul',4,'Adet'],
        ])]),'labels.xlsx')
        self.assertEqual(self.route('TOP').flow.code,'wood')
        self.assertEqual(self.route('LEG').flow.code,'casting')
        for line in MaterialRequirement.objects.filter(source=batch):
            self.assertEqual(line.component_kind,'Mamul')
            self.assertEqual(line.raw['Bileşen Türü'],'Mamul')
            self.assertEqual(line.component.kind,'finished')

    def test_raw_parts_and_main_product_do_not_receive_component_routes(self):
        import_catalog(workbook([('Sehpalar',[
            [1,'P','Sehpa','TOP','Masa tablası','Yarı Mamul',1,'Adet'],
            [2,'P','Sehpa','RAW','Pik','Hammadde',5,'KG'],
            [3,'P','Sehpa','PART','Vida','Parça',4,'Adet'],
        ])]),'scope.xlsx')
        self.assertFalse(ProductionRoute.objects.filter(product__code__in=['P','RAW','PART']).exists())
        for product in Product.objects.filter(code__in=['P','RAW','PART']):
            self.assertEqual(product.production_area,'')
            self.assertEqual(product.supply_method,'')

    def test_blank_component_name_is_left_for_review(self):
        import_catalog(workbook([('Sehpalar',[[1,'P','Sehpa','EMPTY',None,'Yarı Mamul',1,'Adet']])]),'blank.xlsx')
        result=apply_component_routes()
        self.assertFalse(ProductionRoute.objects.filter(product__code='EMPTY').exists())
        self.assertTrue(any('bileşen adı boş' in message for message in result['skipped']))

    def test_repeat_preserves_existing_route_and_custom_step(self):
        import_catalog(workbook([('Sehpalar',[[1,'P','Sehpa','TOP','Masa tablası','Yarı Mamul',1,'Adet']])]),'repeat.xlsx')
        route=self.route('TOP');step=route.steps.get(sequence=2)
        step.name='Özel tabla üretimi';step.duration_minutes=Decimal('25');step.save()
        before=list(route.steps.values_list('pk','name','duration_minutes'))
        result=apply_component_routes()
        self.assertEqual(result['created'],0)
        self.assertEqual(self.route('TOP').pk,route.pk)
        self.assertEqual(list(route.steps.values_list('pk','name','duration_minutes')),before)

    def test_shared_card_conflict_preserves_prior_assignment(self):
        import_catalog(workbook([('Sehpalar',[[1,'P','Sehpa','SHARED','Masa tablası','Yarı Mamul',1,'Adet']])]),'first.xlsx')
        old=self.route('SHARED')
        import_catalog(workbook([('Soba',[[1,'P2','Soba','SHARED','Masa tablası','Yarı Mamul',1,'Adet']])]),'second.xlsx')
        result=apply_component_routes()
        self.assertEqual(self.route('SHARED').pk,old.pk)
        self.assertEqual(self.route('SHARED').flow.code,'wood')
        self.assertTrue(any('SHARED' in message and 'farklı rota' in message for message in result['skipped']))

    def test_mamul_tabletop_is_displayed_in_semifinished_section(self):
        batch,_=import_catalog(workbook([('Sehpalar',[[1,'P','Sehpa','TOP','Masa tablası','Mamul',1,'Adet']])]),'display.xlsx')
        user=User.objects.create_superuser('sehpa-display',password=None);self.client.force_login(user)
        product=Product.objects.get(code='P')
        response=self.client.get(reverse('product_detail',args=[product.pk]))
        rows=next(section['rows'] for section in response.context['sections'] if section['title']=='Yarı mamuller')
        self.assertEqual([row['line'].component_code for row in rows],['TOP'])
        self.assertContains(response,'Ahşap üretimi')
