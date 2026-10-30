from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from erp import views
from erp.order_views import product_options, cancel_order_line
from erp import inventory_views
from erp import operational_views
from erp.reports import reports

admin.site.site_header = 'Kural Döküm • Kullanıcı ve yetki yönetimi'
admin.site.site_title = 'Kural Döküm'
admin.site.index_title = 'Yönetim'
urlpatterns = [
    path('raporlar/',reports,name='reports'),
    path('stok-karti/<int:pk>/',operational_views.stock_card,name='stock_card_detail'),
    path('stok-belgesi/yeni/',operational_views.create_document,name='create_stock_document'),
    path('stok-belgesi/<int:pk>/',operational_views.document_detail,name='stock_document_detail'),
    path('siparis/<int:pk>/takip/',operational_views.order_tracking,name='order_tracking'),
    path('siparis-kalemi/<int:pk>/rezervasyon/',operational_views.reserve_line,name='reserve_order_line'),
    path('rezervasyon/<int:pk>/serbest/',operational_views.release_stock,name='release_stockreservation'),
    path('uretim-isi/<int:pk>/iptal/',operational_views.cancel_production_job,name='cancel_production_job'),
    path('uretim-stok-fisi/<int:pk>/ters/',operational_views.reverse_production_posting,name='reverse_production_posting'),
    path('', views.dashboard, name='dashboard'),
    path('giris/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('cikis/', auth_views.LogoutView.as_view(), name='logout'),
    path('siparis-urun-secimi/', product_options, name='order_product_options'),
    path('siparis-kalemi/<int:pk>/iptal/',cancel_order_line,name='cancel_order_line'),
    path('tuketim-recetesi/<int:pk>/',inventory_views.consumption_recipe_detail,name='consumption_recipe_detail'),
    path('recete/<int:pk>/tuketim-kopyasi/',inventory_views.copy_consumption,name='copy_consumption'),
    path('islem-gecmisi/<int:pk>/',inventory_views.audit_detail,name='audit_detail'),
    path('uretim-stok-fisi/<int:pk>/',inventory_views.production_posting_detail,name='production_posting_detail'),
    path('yonetim/', admin.site.urls),
    path('urun/<int:pk>/', views.product_detail, name='product_detail'),
    path('excel-aktar/', views.catalog_upload, name='catalog_upload'),
    path('excel-aktarim/<int:pk>/', views.catalog_report, name='catalog_report'),
    path('recete/<int:pk>/', views.recipe_detail, name='recipe_detail'),
    path('uretim-akisi/<int:pk>/', views.flow_detail, name='flow_detail'),
    path('rota/<int:pk>/', views.route_detail, name='route_detail'),
    path('uretim-isi/<int:pk>/', views.production_job, name='production_job'),
    path('modul/<slug:slug>/', views.listing, name='listing'),
    path('modul/<slug:slug>/yeni/', views.edit, name='create'),
    path('modul/<slug:slug>/<int:pk>/', views.edit, name='edit'),
]
