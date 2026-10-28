from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from erp import views

admin.site.site_header = 'Yeni ERP • Kullanıcı ve yetki yönetimi'
admin.site.site_title = 'Yeni ERP'
admin.site.index_title = 'Yönetim'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('giris/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('cikis/', auth_views.LogoutView.as_view(), name='logout'),
    path('yonetim/', admin.site.urls),
    path('modul/<slug:slug>/', views.listing, name='listing'),
    path('modul/<slug:slug>/yeni/', views.edit, name='create'),
    path('modul/<slug:slug>/<int:pk>/', views.edit, name='edit'),
]
