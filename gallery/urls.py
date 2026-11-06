from django.urls import path
from . import views

urlpatterns = [
    path('', views.images_show, name='images_show'),
    path('catalog/', views.catalog_view, name='catalog'),
    path('catalog/<slug:slug>/', views.product_detail, name='product_detail'),
    path('catalog/<slug:slug>/quote/', views.request_quote, name='request_quote'),
    path('my-products/', views.my_products, name='my_products'),
    path('my-banners/', views.my_banners, name='my_banners'),
    path('banners/add/', views.create_banner, name='create_banner'),
    path('banners/<int:pk>/edit/', views.edit_banner, name='edit_banner'),
    path('banners/<int:pk>/toggle/', views.toggle_banner, name='toggle_banner'),
    path('products/add/', views.create_product, name='create_product'),
    path('products/<int:pk>/edit/', views.edit_product, name='edit_product'),
    path('products/<int:pk>/toggle/', views.toggle_product, name='toggle_product'),
    path('quotes/', views.buyer_quotes, name='buyer_quotes'),
    path('quotes/incoming/', views.seller_quotes, name='seller_quotes'),
    path('quotes/<int:pk>/respond/', views.respond_to_quote, name='respond_to_quote'),
    path('upload/', views.upload_image, name='upload_image'),
    path('my-images/', views.my_images, name='my_images'),
    path('delete/<int:pk>/', views.delete_image, name='delete_image'),
]