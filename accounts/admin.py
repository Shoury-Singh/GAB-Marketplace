from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from .models import (
    BuyerAccountData,
    IndustryAccountData,
    PincodeLocation,
    ShopkeeperAccountData,
    User,
    UserRole,
    WholesalerAccountData,
)


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0


class RoleAccountDataAdmin(admin.ModelAdmin):
    list_display = ('user', 'account_created_at', 'role_added_at')
    list_select_related = ('user',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('user', 'account_created_at', 'role_added_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(
    [
        BuyerAccountData,
        WholesalerAccountData,
        ShopkeeperAccountData,
        IndustryAccountData,
    ],
    RoleAccountDataAdmin,
)


@admin.register(PincodeLocation)
class PincodeLocationAdmin(admin.ModelAdmin):
    list_display = ('pincode', 'place_name', 'state', 'latitude', 'longitude', 'updated_at')
    search_fields = ('pincode', 'place_name', 'state')
    list_filter = ('state',)
    readonly_fields = ('updated_at',)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'role', 'business_name', 'city', 'pincode', 'shop_is_open', 'is_verified', 'is_active', 'date_joined')
    list_filter = ('role', 'is_verified', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('username', 'email', 'business_name', 'phone', 'city', 'pincode')
    ordering = ('-date_joined',)
    inlines = [UserRoleInline]

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email', 'phone')}),
        (_('Business info'), {'fields': ('role', 'business_name', 'address', 'city', 'state', 'pincode', 'shop_is_open', 'shop_status_changed_at', 'is_verified')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'role', 'business_name', 'password1', 'password2', 'is_staff', 'is_active'),
        }),
    )