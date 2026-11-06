from django import forms

from .models import Product, ProductImage
from .product_images import prepare_product_image
from .products import main_products


class ProductForm(forms.ModelForm):
    photo = forms.FileField(label='Ürün resmi', required=False,
        widget=forms.FileInput(attrs={'accept': '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp'}),
        help_text='JPG, PNG veya WebP; en fazla 5 MB. Yeni bir resim seçerseniz mevcut resmin yerine geçer.')
    remove_photo = forms.BooleanField(label='Mevcut ürün resmini sil', required=False)
    photo_token = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Product
        fields = '__all__'

    def __init__(self, *args, can_manage_image=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.image_eligible = not self.instance.pk or main_products().filter(pk=self.instance.pk).exists()
        self.can_manage_image = can_manage_image
        self.current_picture = ProductImage.objects.only('product_id', 'revision').filter(product=self.instance).first() if self.instance.pk else None
        if not self.image_eligible or not can_manage_image:
            for field in ('photo', 'remove_photo', 'photo_token'):
                self.fields.pop(field)
        else:
            self.initial['photo_token'] = str(self.current_picture.revision) if self.current_picture else ''
            if not self.current_picture:
                self.fields.pop('remove_photo')

    def clean_photo(self):
        upload = self.cleaned_data.get('photo')
        return prepare_product_image(upload) if upload else None

    def clean(self):
        data = super().clean()
        requested = bool(self.files.get('photo')) or forms.BooleanField(required=False).clean(self.data.get('remove_photo'))
        if requested and (not self.image_eligible or not self.can_manage_image):
            raise forms.ValidationError('Resim işlemi yalnızca yetkili kullanıcılar tarafından ana ürün kartlarında yapılabilir.')
        if requested:
            current = str(self.current_picture.revision) if self.current_picture else ''
            if data.get('photo_token', '') != current:
                raise forms.ValidationError('Ürün resmi başka bir işlemde değişti. Güncel kartı açıp tekrar deneyin.')
        if data.get('photo') and data.get('remove_photo'):
            raise forms.ValidationError('Aynı anda yeni resim yükleme ve resmi silme seçilemez.')
        if data.get('photo') and data.get('kind', '') not in ('', 'finished'):
            self.add_error('photo', 'Resim yalnızca ana ürünlere eklenebilir.')
        if self.current_picture and data.get('kind', '') not in ('', 'finished'):
            self.add_error('kind', 'Resimli kart ana ürün olmalıdır. Önce resmi kaldırıp kaydedin, ardından kart türünü değiştirin.')
        return data
