from django.contrib import admin
from .models import GalleryImage, Product, QuoteRequest, SellerBanner


@admin.register(GalleryImage)
class GalleryImageAdmin(admin.ModelAdmin):
    list_display = ('title', 'uploaded_by', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at', 'uploaded_by__role')
    search_fields = ('title', 'description', 'uploaded_by__username', 'uploaded_by__business_name')
    readonly_fields = ('created_at', 'updated_at')
    list_editable = ('is_active',)
    ordering = ('-created_at',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'category', 'price', 'inventory', 'is_active')
    list_filter = ('category', 'is_active', 'owner__role')
    search_fields = ('name', 'supplier', 'owner__username', 'owner__business_name')
    readonly_fields = ('created_at', 'updated_at')
    list_editable = ('is_active',)


@admin.register(QuoteRequest)
class QuoteRequestAdmin(admin.ModelAdmin):
    list_display = ('product', 'buyer', 'seller', 'quantity', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('product__name', 'buyer__username', 'seller__username')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SellerBanner)
class SellerBannerAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at', 'owner__role')
    search_fields = ('title', 'owner__username', 'owner__business_name')
    readonly_fields = ('created_at', 'updated_at')
    list_editable = ('is_active',)