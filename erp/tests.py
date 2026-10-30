from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth.models import User, Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse
from .models import Product, Warehouse, StockMovement, Partner, Order, OrderLine, ProductionRoute, RouteStep, stock_balance
from .registry import MODULES, READ_ONLY_MODELS

class ERPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)
        cls.admin = User.objects.create_superuser('yonetici', password='TestOnly-StrongPass-4871')
        cls.viewer = User.objects.create_user('izleyici', password='TestOnly-StrongPass-4871')
        cls.viewer.groups.add(Group.objects.get(name='Gözlemci'))
        cls.depot_user = User.objects.create_user('depocu')
        cls.depot_user.groups.add(Group.objects.get(name='Depo sorumlusu'))
        cls.product = Product.objects.create(code='UR-001', name='Çelik parça', minimum_stock=5)
        cls.warehouse = Warehouse.objects.create(name='Ana depo')
        cls.partner = Partner.objects.create(name='Örnek müşteri', kind='customer')

    def movement(self, kind='in', quantity='10', warehouse=None):
        return StockMovement.objects.create(product=self.product, warehouse=warehouse or self.warehouse, kind=kind, quantity=Decimal(quantity), note='Test belgesi', created_by=self.admin)

    def order(self, number='SP-001'):
        return Order.objects.create(number=number, kind='sale', partner=self.partner, date=date.today())

    def test_anonymous_redirects_to_login(self):
        for url in ['/', '/modul/urunler/', '/modul/stok/yeni/']:
            self.assertEqual(self.client.get(url).status_code,302)

    def test_login_logout(self):
        response = self.client.post(reverse('login'), {'username':'yonetici','password':'TestOnly-StrongPass-4871'})
        self.assertRedirects(response, '/')
        self.assertEqual(self.client.get(reverse('logout')).status_code,405)
        self.assertRedirects(self.client.post(reverse('logout')),reverse('login'))

    def test_all_module_lists_and_forms_render(self):
        self.client.force_login(self.admin)
        for slug in MODULES:
            with self.subTest(slug=slug):
                self.assertEqual(self.client.get(reverse('listing',args=[slug])).status_code,200)
                expected=302 if slug=='stok-belgeleri' else 403 if MODULES[slug][0] in READ_ONLY_MODELS else 200
                self.assertEqual(self.client.get(reverse('create',args=[slug])).status_code,expected)
        self.assertContains(self.client.get('/'), 'Ana kontrol paneli')

    def test_viewer_cannot_write_even_direct_post(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get('/modul/urunler/').status_code,200)
        self.assertEqual(self.client.post('/modul/urunler/yeni/', {'code':'HACK','name':'Yetkisiz'}).status_code,403)
        self.assertEqual(self.client.get(f'/modul/urunler/{self.product.pk}/').status_code,403)
        self.assertFalse(Product.objects.filter(code='HACK').exists())

    def test_warehouse_role_cannot_see_partners_or_users(self):
        self.client.force_login(self.depot_user)
        self.assertEqual(self.client.get('/modul/cariler/').status_code,403)
        self.assertEqual(self.client.get('/yonetim/auth/user/').status_code,302)
        self.assertNotContains(self.client.get('/'), 'Müşteri ve tedarikçiler')

    def test_user_without_role_sees_no_business_data(self):
        user = User.objects.create_user('rolsuz')
        self.client.force_login(user)
        self.assertContains(self.client.get('/'), 'henüz bir rol atanmamış')
        self.assertEqual(self.client.get('/modul/stok/').status_code,403)

    def test_product_creation_search_and_duplicate(self):
        self.client.force_login(self.admin)
        payload = {'code':'UR-002','name':'Bakır tel','unit':'m','minimum_stock':'2','active':'on'}
        self.assertRedirects(self.client.post('/modul/urunler/yeni/',payload),'/modul/urunler/')
        self.assertContains(self.client.post('/modul/urunler/yeni/',payload),'zaten mevcut',status_code=200)
        self.assertContains(self.client.get('/modul/urunler/?q=Bakır'),'Bakır tel')
        self.assertNotContains(self.client.get('/modul/urunler/?q=Bakır'),'Çelik parça')

    def test_stock_entry_exit_and_per_warehouse_balance(self):
        self.movement()
        self.movement('out','3')
        other = Warehouse.objects.create(name='İkinci depo')
        self.movement('in','4',other)
        self.assertEqual(stock_balance(self.product,self.warehouse),Decimal('7'))
        self.assertEqual(stock_balance(self.product),Decimal('11'))

    def test_stock_rejects_negative_zero_and_insufficient(self):
        self.movement()
        for quantity in ['0','-1']:
            with self.assertRaises(ValidationError): self.movement('in',quantity)
        with self.assertRaises(ValidationError): self.movement('out','11')
        self.assertEqual(StockMovement.objects.count(),1)
        self.assertEqual(stock_balance(self.product),10)

    def test_stock_cannot_be_edited_or_deleted(self):
        movement = self.movement()
        movement.note = 'Değişiklik'
        with self.assertRaises(ValidationError): movement.save()
        with self.assertRaises(ValidationError): movement.delete()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(f'/modul/stok/{movement.pk}/',{}).status_code,403)

    def test_stock_form_attributes_actor_and_handles_insufficient(self):
        self.client.force_login(self.depot_user)
        payload = {'product':self.product.pk,'warehouse':self.warehouse.pk,'kind':'in','quantity':'8','note':'Açılış'}
        self.assertRedirects(self.client.post('/modul/stok/yeni/',payload),'/modul/stok/')
        self.assertEqual(StockMovement.objects.get().created_by,self.depot_user)
        payload.update(kind='out',quantity='9')
        self.assertContains(self.client.post('/modul/stok/yeni/',payload),'Yetersiz stok')
        self.assertEqual(stock_balance(self.product),8)

    def test_order_total_uses_decimal_and_does_not_change_stock(self):
        order = self.order()
        OrderLine.objects.create(order=order,product=self.product,quantity=Decimal('3'),unit_price=Decimal('10.25'))
        self.assertEqual(order.total,Decimal('30.75'))
        self.assertEqual(stock_balance(self.product),0)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/modul/siparisler/'),'30,75')

    def test_order_date_partner_and_empty_approval_validation(self):
        order = self.order()
        order.due_date = date.today()-timedelta(days=1)
        with self.assertRaises(ValidationError): order.full_clean()
        order.due_date = None
        order.kind = 'purchase'
        with self.assertRaises(ValidationError): order.full_clean()
        order.kind = 'sale'
        order.status = 'approved'
        with self.assertRaises(ValidationError): order.full_clean()

    def test_approved_order_line_locked_including_move(self):
        order = self.order()
        line = OrderLine.objects.create(order=order,product=self.product,quantity=1,unit_price=2)
        order.status = 'approved'
        order.full_clean()
        order.save()
        with self.assertRaises(ValidationError): line.full_clean()
        line.order = self.order('SP-002')
        with self.assertRaises(ValidationError): line.full_clean()

    def test_route_step_order_and_duplicate_validation(self):
        route = ProductionRoute.objects.create(name='Kesim rotası',product=self.product)
        RouteStep.objects.create(route=route,sequence=2,name='Paketleme',workstation='Paket',duration_minutes=5)
        RouteStep.objects.create(route=route,sequence=1,name='Kesim',workstation='Testere',duration_minutes=10)
        self.assertEqual(list(route.steps.values_list('sequence',flat=True)),[1,2])
        with self.assertRaises(ValidationError): RouteStep(route=route,sequence=1,name='Tekrar',workstation='Test',duration_minutes=1).full_clean()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/modul/rota-adimlari/'),'Kesim')

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post('/modul/urunler/yeni/',{}).status_code,403)

    def test_roles_repeatable(self):
        before = Group.objects.count()
        call_command('setup_roles',verbosity=0)
        self.assertEqual(Group.objects.count(),before)

    def test_pagination_and_unknown_module(self):
        self.client.force_login(self.admin)
        Product.objects.bulk_create([Product(code=f'P-{n}',name=f'Ürün {n}') for n in range(27)])
        response = self.client.get('/modul/urunler/')
        self.assertEqual(len(response.context['rows']),25)
        self.assertEqual(self.client.get('/modul/bilinmeyen/').status_code,404)

    def test_complete_business_workflow_through_forms(self):
        self.client.force_login(self.admin)
        cases = [
            ('depolar', {'name':'Atölye'}),
            ('cariler', {'name':'Test tedarikçi','kind':'supplier','email':'tedarik@example.test','phone':'555 000 00 00','tax_number':'','address':'İstanbul'}),
            ('siparisler', {'number':'WEB-001','kind':'sale','partner':self.partner.pk,'status':'draft','date':'2026-10-28','due_date':'2026-11-01','currency':'TRY','note':'Test siparişi'}),
            ('rotalar', {'name':'Form rotası','product':self.product.pk,'version':1,'active':'on'}),
        ]
        for slug, payload in cases:
            with self.subTest(slug=slug):
                self.assertRedirects(self.client.post(reverse('create',args=[slug]),payload),reverse('listing',args=[slug]))
        order = Order.objects.get(number='WEB-001')
        route = ProductionRoute.objects.get(name='Form rotası')
        self.assertRedirects(self.client.post('/modul/siparis-kalemleri/yeni/',{'order':order.pk,'product':self.product.pk,'quantity':'2','unit_price':'12.40'}),'/modul/siparis-kalemleri/')
        self.assertRedirects(self.client.post('/modul/rota-adimlari/yeni/',{'route':route.pk,'sequence':1,'name':'Montaj','workstation':'Tezgah 1','duration_minutes':'15','instructions':'Kontrol ederek birleştirin.'}),'/modul/rota-adimlari/')
        payload = cases[2][1] | {'status':'approved'}
        self.assertRedirects(self.client.post(reverse('edit',args=['siparisler',order.pk]),payload),'/modul/siparisler/')
        order.refresh_from_db()
        self.assertEqual(order.status,'approved')
        self.assertEqual(order.total,Decimal('24.80'))
        self.assertEqual(route.steps.count(),1)
        self.assertEqual(stock_balance(self.product),0)

    def test_admin_user_management_requires_superuser(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/yonetim/auth/user/').status_code,200)
        self.assertEqual(self.client.get('/yonetim/auth/group/').status_code,200)
        user = User.objects.create_user('personel',is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/yonetim/auth/user/').status_code,403)
