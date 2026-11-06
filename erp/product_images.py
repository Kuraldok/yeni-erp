"""Validate product photos and save image, thumbnail and audit atomically."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import warnings

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.views.decorators.http import require_safe
from PIL import Image, ImageOps, UnidentifiedImageError

from .audit import with_actor
from .models import Product, ProductImage
from .products import main_products

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}


@dataclass(frozen=True)
class PreparedProductImage:
    image: bytes
    thumbnail: bytes
    content_type: str
    width: int
    height: int


def prepare_product_image(upload):
    if upload.size > MAX_IMAGE_BYTES:
        raise ValidationError('Resim en fazla 5 MB olabilir.')
    if Path(upload.name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
        raise ValidationError('JPG, PNG veya WebP resim seçin.')
    data = upload.read(MAX_IMAGE_BYTES + 1)
    upload.seek(0)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError('Resim en fazla 5 MB olabilir.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in ALLOWED_FORMATS:
                    raise ValidationError('JPG, PNG veya WebP resim seçin.')
                if original.width * original.height > MAX_IMAGE_PIXELS:
                    raise ValidationError('Resim en fazla 20 milyon piksel olabilir.')
                if getattr(original, 'n_frames', 1) != 1:
                    raise ValidationError('Hareketli resimler desteklenmiyor; tek kare resim seçin.')
                original.verify()
            with Image.open(BytesIO(data)) as original:
                original.load()
                corrected = ImageOps.exif_transpose(original)
                alpha = corrected.mode in ('RGBA', 'LA') or 'transparency' in corrected.info
                image = corrected.convert('RGBA' if alpha else 'RGB')
                image.info.clear()
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                thumbnail = image.copy()
                thumbnail.thumbnail((160, 160), Image.Resampling.LANCZOS)
                image_format = 'PNG' if alpha else 'JPEG'
                options = {'optimize': True} if alpha else {'quality': 85, 'optimize': True}
                full_bytes, small_bytes = BytesIO(), BytesIO()
                image.save(full_bytes, format=image_format, **options)
                thumbnail.save(small_bytes, format=image_format, **options)
                return PreparedProductImage(full_bytes.getvalue(), small_bytes.getvalue(),
                    'image/png' if alpha else 'image/jpeg', image.width, image.height)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError('Dosya geçerli ve okunabilir bir JPG, PNG veya WebP resmi olmalıdır.') from exc


def product_image_url(picture, variant='thumb'):
    return reverse('product_image', args=[picture.product_id, picture.revision, variant])


@with_actor
@transaction.atomic
def save_product_image(product, upload, remove, expected_revision, actor):
    if not upload and not remove:
        return
    if not actor or not actor.has_perms(['erp.view_product', 'erp.change_product']):
        raise PermissionDenied('Ürün resmi düzenleme yetkiniz yok.')
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not main_products().filter(pk=product.pk).exists():
        raise ValidationError('Resim yalnızca ana ürünlere eklenebilir veya ana ürünlerden silinebilir.')
    picture = ProductImage.objects.select_for_update().filter(product=product).first()
    if str(picture.revision if picture else '') != str(expected_revision or ''):
        raise ValidationError('Ürün resmi başka bir işlemde değişti. Güncel kartı açıp tekrar deneyin.')
    if upload and remove:
        raise ValidationError('Aynı anda yeni resim yükleme ve resmi silme seçilemez.')
    if remove:
        if picture:
            picture._audit_reason = 'Ürün resmi silindi.'
            picture.delete()
        return
    if not isinstance(upload, PreparedProductImage):
        raise ValidationError('Yüklenen resim doğrulanmalıdır.')
    if picture is None:
        picture = ProductImage(product=product, created_by=actor)
        reason = 'Ürün resmi eklendi.'
    else:
        reason = 'Ürün resmi değiştirildi.'
    picture.image, picture.thumbnail = upload.image, upload.thumbnail
    picture.content_type = upload.content_type
    picture.width, picture.height = upload.width, upload.height
    picture.revision = uuid4()
    picture.updated_by = actor
    picture._audit_reason = reason
    picture.full_clean()
    picture.save()


@login_required
@require_safe
def product_image(request, pk, revision, variant):
    if not request.user.has_perm('erp.view_product'):
        raise PermissionDenied
    if variant not in ('thumb', 'full') or not main_products().filter(pk=pk).exists():
        raise Http404
    picture = ProductImage.objects.only('revision', 'content_type').filter(product_id=pk, revision=revision).first()
    if picture is None:
        raise Http404
    etag = f'"{picture.revision.hex}-{variant}"'
    if request.headers.get('If-None-Match') == etag:
        response = HttpResponseNotModified()
    else:
        data = picture.thumbnail if variant == 'thumb' else picture.image
        if not data:
            raise Http404
        response = HttpResponse(bytes(data), content_type=picture.content_type)
        extension = 'png' if picture.content_type == 'image/png' else 'jpg'
        response['Content-Disposition'] = f'inline; filename="product-{pk}.{extension}"'
    response['ETag'] = etag
    patch_cache_control(response, private=True, max_age=0, must_revalidate=True)
    patch_vary_headers(response, ['Cookie'])
    return response
