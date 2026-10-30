from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from erp import views
from erp.order_views import product_options

admin.site.site_header = 'Kural Döküm • Kullanıcı ve yetki yönetimi'
admin.site.site_title = 'Kural Döküm'
admin.site.index_title = 'Yönetim'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('giris/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('cikis/', auth_views.LogoutView.as_view(), name='logout'),
    path('siparis-urun-secimi/', product_options, name='order_product_options'),
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
