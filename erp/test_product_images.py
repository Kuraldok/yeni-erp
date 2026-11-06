from hashlib import sha256
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from PIL import Image

from .audit import audit_actor
from .models import (AuditEvent, CatalogImport, MaterialRecipe, MaterialRequirement, Product,
    ProductImage, ProductionFlow, ProductionJob, StockMovement)
from .product_images import MAX_IMAGE_BYTES, prepare_product_image, product_image_url, save_product_image
from .workflows import ensure_workflows


def photo(format='PNG', size=(80, 40), color=None, **options):
    alpha = format == 'PNG'
    image = Image.new('RGBA' if alpha else 'RGB', size, color or ((25, 110, 120, 120) if alpha else (25, 110, 120)))
    output = BytesIO()
    image.save(output, format=format, **options)
    name = {'PNG': 'photo.png', 'JPEG': 'photo.jpg', 'WEBP': 'photo.webp'}[format]
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/' + format.lower())


class ProductImageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('photo-admin', password='Fixture-only-photo-6819')
        cls.viewer = User.objects.create_user('photo-viewer')
        cls.viewer.user_permissions.add(Permission.objects.get(codename='view_product', content_type__app_label='erp'))
        cls.maker = User.objects.create_user('photo-maker')
        cls.maker.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=['view_product', 'add_product']))
        cls.no_view = User.objects.create_user('photo-no-view')
        cls.main = Product.objects.create(code='PHOTO-01', name='Ana ürün', kind='finished', category='Masalar')
        cls.other = Product.objects.create(code='PHOTO-02', name='Resimsiz ana ürün', kind='finished')
        cls.raw = Product.objects.create(code='PHOTO-RAW', name='Hammadde', kind='raw', unit='kg')
        cls.semi = Product.objects.create(code='PHOTO-SEMI', name='Yarı mamul', kind='semi')
        cls.part = Product.objects.create(code='PHOTO-PART', name='Parça', kind='part')
        cls.finished_component = Product.objects.create(code='PHOTO-COMP', name='Mamul etiketli bileşen', kind='finished')
        batch = CatalogImport.objects.create(filename='Sentetik.xlsx', checksum='photo-fixture')
        recipe = MaterialRecipe.objects.create(source=batch, sheet='Masalar', parent_code=cls.main.code,
            parent_name=cls.main.name, product=cls.main)
        MaterialRequirement.objects.create(source=batch, recipe=recipe, sheet='Masalar', row_number=4,
            parent_code=cls.main.code, component_code=cls.finished_component.code,
            component=cls.finished_component, component_name=cls.finished_component.name,
            component_kind='Mamul', unit='Adet', quantity=1, raw={'Özgün': 'Kaynak değeri'})

    def setUp(self):
        self.client.force_login(self.admin)

    def payload(self, product=None, **changes):
        product = product or self.main
        picture = ProductImage.objects.filter(product=product).first() if product.pk else None
        data = dict(code=product.code, name=product.name, kind=product.kind, unit=product.unit,
            category=product.category, minimum_stock='0', active='on',
            supply_method=product.supply_method, production_area=product.production_area,
            photo_token=str(picture.revision) if picture else '')
        data.update(changes)
        return data

    def edit(self, product=None, **changes):
        product = product or self.main
        return self.client.post(reverse('edit', args=['urunler', product.pk]), self.payload(product, **changes))

    def add_photo(self, product=None, **changes):
        product = product or self.main
        response = self.edit(product, photo=photo(), **changes)
        self.assertEqual(response.status_code, 302)
        return ProductImage.objects.get(product=product)

    def test_upload_shows_thumbnail_in_the_same_cell_as_product_code_in_both_lists(self):
        picture = self.add_photo()
        for slug in ('urunler', 'stok-kartlari'):
            response = self.client.get(reverse('listing', args=[slug]))
            row = next(row for row in response.context['rows'] if row['pk'] == self.main.pk)
            self.assertEqual(row['image_url'], product_image_url(picture))
            self.assertRegex(response.content.decode(), r'class="product-code-display"><img[^>]+><span>PHOTO-01</span></a>')
            self.assertNotContains(response, 'Resimler sekmesi')
            self.assertEqual(next(row for row in response.context['rows'] if row['pk'] == self.other.pk)['image_url'], '')

    def test_details_and_embedded_tabs_show_photo_next_to_code_without_new_links(self):
        picture = self.add_photo()
        for route in ('product_detail', 'stock_card_detail'):
            url = reverse(route, args=[self.main.pk])
            for headers in ({}, {'X-Product-Tab': '1'}):
                response = self.client.get(url, {'tab': 'overview'}, headers=headers)
                self.assertEqual(response.context['product_image_url'], product_image_url(picture))
                self.assertContains(response, 'product-code-display product-code-heading')
                self.assertContains(response, 'PHOTO-01 ürün resmi')
                self.assertNotContains(response, 'target="_blank"')

    def test_new_main_product_can_be_saved_with_its_photo(self):
        new = Product(code='PHOTO-NEW', name='Yeni ana ürün', kind='finished')
        response = self.client.post(reverse('create', args=['urunler']), self.payload(new, photo=photo()))
        self.assertEqual(response.status_code, 302)
        picture = ProductImage.objects.get(product__code=new.code)
        self.assertEqual((picture.created_by_id, picture.updated_by_id), (self.admin.pk, self.admin.pk))
        event = AuditEvent.objects.get(model_label='erp.productimage', object_id=str(picture.pk), action='create')
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.reason, 'Ürün resmi eklendi.')

    def test_edit_form_is_multipart_and_keeps_current_photo_until_explicitly_changed(self):
        picture = self.add_photo()
        response = self.client.get(reverse('edit', args=['stok-kartlari', self.main.pk]))
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'Mevcut ürün resmini sil')
        self.assertContains(response, 'product-code-input')
        self.assertEqual(response.context['form'].initial['photo_token'], str(picture.revision))
        before = ProductImage.objects.filter(pk=picture.pk).values().get()
        self.assertEqual(self.edit().status_code, 302)
        self.assertEqual(ProductImage.objects.filter(pk=picture.pk).values().get(), before)

    def test_replacing_photo_changes_revision_and_audits_hashes_without_image_bytes(self):
        picture = self.add_photo()
        old_url = product_image_url(picture)
        old_data = bytes(picture.image)
        old_revision = picture.revision
        response = self.edit(photo=photo('JPEG', color=(180, 20, 40)))
        self.assertEqual(response.status_code, 302)
        picture.refresh_from_db()
        self.assertNotEqual(picture.revision, old_revision)
        self.assertNotEqual(bytes(picture.image), old_data)
        self.assertEqual(ProductImage.objects.count(), 1)
        self.assertEqual(self.client.get(old_url).status_code, 404)
        self.assertEqual(self.client.get(product_image_url(picture)).status_code, 200)
        event = AuditEvent.objects.filter(model_label='erp.productimage', object_id=str(picture.pk), action='update').latest('pk')
        self.assertEqual(event.changes['image']['before']['sha256'], sha256(old_data).hexdigest())
        self.assertEqual(set(event.changes['image']['after']), {'bytes', 'sha256'})
        self.assertEqual(event.reason, 'Ürün resmi değiştirildi.')
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertIsNotNone(event.created_at)

    def test_delete_removes_photo_and_preserves_product_recipe_and_stock_records(self):
        picture = self.add_photo()
        old_url = product_image_url(picture)
        cards = list(Product.objects.order_by('pk').values())
        sources = list(MaterialRequirement.objects.values())
        response = self.edit(remove_photo='on')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductImage.objects.exists())
        self.assertEqual(self.client.get(old_url).status_code, 404)
        self.assertEqual(list(Product.objects.order_by('pk').values()), cards)
        self.assertEqual(list(MaterialRequirement.objects.values()), sources)
        self.assertEqual(StockMovement.objects.count(), 0)
        event = AuditEvent.objects.get(model_label='erp.productimage', object_id=str(picture.pk), action='delete')
        self.assertEqual((event.actor_id, event.reason), (self.admin.pk, 'Ürün resmi silindi.'))
        response = self.client.get(reverse('listing', args=['urunler']))
        self.assertNotContains(response, 'class="product-thumbnail"')

    def test_only_main_products_accept_photos_and_components_do_not_show_upload_controls(self):
        for product in (self.raw, self.semi, self.part, self.finished_component):
            with self.subTest(code=product.code):
                response = self.client.get(reverse('edit', args=['stok-kartlari', product.pk]))
                self.assertNotIn('photo', response.context['form'].fields)
                response = self.edit(product, photo=photo(), name='Kaydedilmemeli')
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'ana ürün')
                product.refresh_from_db()
                self.assertNotEqual(product.name, 'Kaydedilmemeli')
        self.assertFalse(ProductImage.objects.exists())

    def test_new_raw_card_cannot_be_created_with_a_photo(self):
        card = Product(code='PHOTO-BAD-RAW', name='Ham', kind='raw', unit='kg')
        response = self.client.post(reverse('create', args=['stok-kartlari']), self.payload(card, photo=photo()))
        self.assertEqual(response.status_code, 200)
        self.assertIn('photo', response.context['form'].errors)
        self.assertFalse(Product.objects.filter(code=card.code).exists())

    def test_bad_extension_spoofed_and_truncated_images_keep_existing_photo(self):
        picture = self.add_photo()
        before = bytes(picture.image), picture.revision
        for upload in (SimpleUploadedFile('bad.svg', b'<svg/>'), SimpleUploadedFile('bad.jpg', b'<html>text</html>'),
                SimpleUploadedFile('bad.jpg', photo('JPEG').read()[:40])):
            with self.subTest(name=upload.name):
                response = self.edit(photo=upload)
                self.assertEqual(response.status_code, 200)
                self.assertIn('photo', response.context['form'].errors)
                picture.refresh_from_db()
                self.assertEqual((bytes(picture.image), picture.revision), before)

    def test_size_and_pixel_limits_are_checked_before_decoding(self):
        response = self.edit(photo=SimpleUploadedFile('large.jpg', b'X' * (MAX_IMAGE_BYTES + 1)))
        self.assertContains(response, '5 MB')
        with patch('erp.product_images.MAX_IMAGE_PIXELS', 100):
            response = self.edit(photo=photo(size=(11, 10)))
        self.assertContains(response, '20 milyon piksel')
        self.assertFalse(ProductImage.objects.exists())

    def test_animated_png_is_rejected(self):
        frames = [Image.new('RGB', (20, 20), color) for color in ('red', 'blue')]
        data = BytesIO()
        frames[0].save(data, format='PNG', save_all=True, append_images=frames[1:], duration=100, loop=0)
        response = self.edit(photo=SimpleUploadedFile('animated.png', data.getvalue()))
        self.assertContains(response, 'Hareketli resimler desteklenmiyor')
        self.assertFalse(ProductImage.objects.exists())

    def test_jpeg_png_and_webp_are_normalized_to_bounded_images_and_thumbnails(self):
        for format in ('JPEG', 'PNG', 'WEBP'):
            with self.subTest(format=format):
                self.assertEqual(self.edit(photo=photo(format, size=(2000, 1000))).status_code, 302)
                picture = ProductImage.objects.get(product=self.main)
                self.assertEqual((picture.width, picture.height), (1600, 800))
                with Image.open(BytesIO(bytes(picture.thumbnail))) as image:
                    self.assertEqual(image.size, (160, 80))
                with Image.open(BytesIO(bytes(picture.image))) as image:
                    self.assertEqual(image.size, (1600, 800))
                    if format == 'PNG': self.assertEqual(image.getpixel((0, 0))[3], 120)

    def test_exif_rotation_is_applied_and_source_metadata_is_removed(self):
        exif = Image.Exif(); exif[274] = 6; exif[315] = 'Sentetik kaynak yazarı'
        response = self.edit(photo=photo('JPEG', exif=exif))
        self.assertEqual(response.status_code, 302)
        picture = ProductImage.objects.get(product=self.main)
        self.assertEqual((picture.width, picture.height), (40, 80))
        with Image.open(BytesIO(bytes(picture.image))) as image:
            self.assertEqual(len(image.getexif()), 0)

    def test_viewer_can_read_images_with_correct_headers_but_cannot_edit_them(self):
        picture = self.add_photo()
        self.client.force_login(self.viewer)
        for variant in ('thumb', 'full'):
            url = product_image_url(picture, variant)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('Cookie', response['Vary'])
            cached = self.client.get(url, headers={'If-None-Match': response['ETag']})
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(self.client.head(url).status_code, 200)
        self.assertEqual(self.edit(photo=photo()).status_code, 403)
        self.assertEqual(self.edit(remove_photo='on').status_code, 403)
        with self.assertRaises(PermissionDenied):
            save_product_image(self.main, prepare_product_image(photo()), False, '', None)

    def test_anonymous_and_users_without_product_access_cannot_read_images(self):
        picture = self.add_photo()
        url = product_image_url(picture)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.no_view)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_add_only_user_cannot_attach_photo_and_unsafe_requests_require_csrf(self):
        self.client.force_login(self.maker)
        response = self.client.get(reverse('create', args=['urunler']))
        self.assertNotIn('photo', response.context['form'].fields)
        card = Product(code='MAKER-PHOTO', name='Yeni kart', kind='finished')
        response = self.client.post(reverse('create', args=['urunler']), self.payload(card, photo=photo()))
        self.assertEqual(response.status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        response = csrf_client.post(reverse('edit', args=['urunler', self.main.pk]), self.payload(photo=photo()))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProductImage.objects.exists())

    def test_image_url_cannot_mutate_photo_or_serve_invalid_variants_and_components(self):
        picture = self.add_photo()
        self.assertEqual(self.client.post(product_image_url(picture)).status_code, 405)
        self.assertEqual(self.client.get(product_image_url(picture, 'invalid')).status_code, 404)
        # A legacy or administrative attachment must not expose component photos.
        picture.product = self.finished_component; picture.save()
        self.assertEqual(self.client.get(product_image_url(picture)).status_code, 404)

    def test_stale_upload_and_delete_do_not_overwrite_current_picture_or_card(self):
        picture = self.add_photo()
        old_token = str(picture.revision)
        self.assertEqual(self.edit(photo=photo('JPEG')).status_code, 302)
        picture.refresh_from_db()
        expected = bytes(picture.image), picture.revision
        for changes in ({'photo': photo()}, {'remove_photo': 'on'}):
            response = self.edit(photo_token=old_token, name='Eski form değişikliği', **changes)
            self.assertContains(response, 'başka bir işlemde değişti')
            picture.refresh_from_db(); self.main.refresh_from_db()
            self.assertEqual((bytes(picture.image), picture.revision), expected)
            self.assertEqual(self.main.name, 'Ana ürün')
        with self.assertRaises(ValidationError):
            save_product_image(self.main, prepare_product_image(photo()), False, old_token, self.admin)

    def test_upload_and_delete_together_are_rejected(self):
        picture = self.add_photo()
        response = self.edit(photo=photo(), remove_photo='on')
        self.assertContains(response, 'Aynı anda')
        self.assertEqual(ProductImage.objects.get(product=self.main).revision, picture.revision)

    def test_failed_image_write_rolls_back_card_photo_and_audit_together(self):
        picture = self.add_photo()
        before = ProductImage.objects.filter(pk=picture.pk).values().get()
        count = AuditEvent.objects.count()
        original = ProductImage.save
        def fail_after_save(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise ValidationError('Sentetik resim kayıt hatası')
        with patch.object(ProductImage, 'save', fail_after_save):
            response = self.edit(photo=photo('JPEG'), name='Kaydedilmemeli')
        self.assertContains(response, 'Sentetik resim kayıt hatası')
        self.main.refresh_from_db()
        self.assertEqual(self.main.name, 'Ana ürün')
        self.assertEqual(ProductImage.objects.filter(pk=picture.pk).values().get(), before)
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_failed_delete_restores_photo_and_audit(self):
        picture = self.add_photo()
        before = ProductImage.objects.filter(pk=picture.pk).values().get()
        count = AuditEvent.objects.count()
        from .signals import event
        def fail_after_delete(instance, action, *args):
            event(instance, action, *args)
            if isinstance(instance, ProductImage) and action == 'delete': raise ValidationError('Sentetik silme hatası')
        with patch('erp.signals.event', side_effect=fail_after_delete):
            response = self.edit(remove_photo='on')
        self.assertContains(response, 'Sentetik silme hatası')
        self.assertEqual(ProductImage.objects.filter(pk=picture.pk).values().get(), before)
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_running_or_draft_production_does_not_prevent_independent_photo_changes(self):
        ensure_workflows()
        job = ProductionJob.objects.create(number='PHOTO-JOB', product=self.main, quantity=1, unit='adet',
            flow=ProductionFlow.objects.get(code='wood'), stock_mode='tracking', created_by=self.admin)
        from .production import advance_job
        for state in ('draft', 'running'):
            if state == 'running': advance_job(job.pk, self.admin, 'start')
            before = ProductionJob.objects.filter(pk=job.pk).values().get()
            self.add_photo()
            self.assertEqual(ProductionJob.objects.filter(pk=job.pk).values().get(), before)

    def test_list_fetches_image_metadata_in_one_query_without_loading_binary_images(self):
        self.add_photo()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('listing', args=['urunler']))
        self.assertEqual(response.status_code, 200)
        selects = [query['sql'] for query in queries if 'FROM "erp_productimage"' in query['sql']]
        self.assertEqual(len(selects), 1)
        self.assertNotIn('"image"', selects[0])
        self.assertNotIn('"thumbnail"', selects[0])
        self.assertIn('"revision"', selects[0])

    def test_picture_must_be_removed_before_changing_card_to_component_type(self):
        self.add_photo()
        response = self.edit(kind='semi')
        self.assertIn('kind', response.context['form'].errors)
        self.main.refresh_from_db()
        self.assertEqual(self.main.kind, 'finished')
        self.assertTrue(ProductImage.objects.filter(product=self.main).exists())
