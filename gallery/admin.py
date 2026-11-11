from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import (
    GalleryImage,
    Order,
    OrderMessage,
    PaymentAttempt,
    PaymentWebhookEvent,
    CommissionPlan,
    CommissionLedger,
    CommissionStatement,
    SellerPayoutProfile,
    Product,
    ProductInteraction,
    QuoteRequest,
    SellerBanner,
)


@admin.register(ProductInteraction)
class ProductInteractionAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'user', 'product', 'category', 'created_at')
    list_filter = ('event_type', 'category', 'created_at')
    search_fields = ('user__username', 'product__name', 'search_hash')
    readonly_fields = ('user', 'product', 'event_type', 'category', 'search_hash', 'created_at')
    ordering = ('-created_at',)


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


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'buyer', 'seller', 'quantity', 'unit_price', 'total_amount', 'status', 'payment_status', 'updated_at')
    list_filter = ('status', 'payment_status', 'created_at')
    search_fields = ('product__name', 'buyer__username', 'seller__username')
    readonly_fields = (
        'source_quote',
        'status',
        'unit_price',
        'total_amount',
        'product_snapshot',
        'payment_status',
        'paid_at',
        'created_at',
        'updated_at',
    )


@admin.register(OrderMessage)
class OrderMessageAdmin(admin.ModelAdmin):
    list_display = ('order', 'sender', 'created_at')
    search_fields = ('body', 'sender__username', 'order__product__name')
    readonly_fields = ('created_at',)


@admin.register(SellerBanner)
class SellerBannerAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at', 'owner__role')
    search_fields = ('title', 'owner__username', 'owner__business_name')
    readonly_fields = ('created_at', 'updated_at')
    list_editable = ('is_active',)



@admin.register(CommissionPlan)
class CommissionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'commission_rate_bps', 'gst_rate_bps', 'is_default', 'is_active')
    list_filter = ('is_default', 'is_active')


@admin.register(SellerPayoutProfile)
class SellerPayoutProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'provider', 'onboarding_status', 'commission_plan', 'updated_at')
    list_filter = ('provider', 'onboarding_status')
    search_fields = ('user__username', 'user__business_name', 'provider_account_id')


class PaymentAttemptAdminForm(forms.ModelForm):
    class Meta:
        model = PaymentAttempt
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [
            (PaymentAttempt.Status.CREATED, PaymentAttempt.Status.CREATED.label),
            (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.PENDING.label),
            (PaymentAttempt.Status.FAILED, PaymentAttempt.Status.FAILED.label),
            (PaymentAttempt.Status.CANCELLED, PaymentAttempt.Status.CANCELLED.label),
        ]


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'provider', 'status', 'amount_paise', 'currency', 'created_at')
    list_filter = ('provider', 'status', 'created_at')
    search_fields = ('provider_order_id', 'provider_payment_id')
    form = PaymentAttemptAdminForm
    readonly_fields = [field.name for field in PaymentAttempt._meta.fields]

    def get_readonly_fields(self, request, obj=None):
        readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status in {PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING}:
            readonly.discard('status')
            readonly.discard('reconciliation_note')
        return tuple(readonly)

    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = PaymentAttempt.objects.get(pk=obj.pk).status
        resolving = (
            previous_status in {PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING}
            and obj.status in {PaymentAttempt.Status.FAILED, PaymentAttempt.Status.CANCELLED}
        )
        if resolving:
            if not obj.reconciliation_note.strip():
                raise ValidationError('Add a note confirming the provider status you checked.')
            obj.reconciled_by = request.user
            obj.reconciled_at = timezone.now()
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if resolving:
                order = Order.objects.select_for_update().get(pk=obj.order_id)
                another_open_attempt = PaymentAttempt.objects.filter(
                    order=order,
                    status__in=[PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING],
                ).exclude(pk=obj.pk).exists()
                if not another_open_attempt and order.payment_status == 'PENDING':
                    order.payment_status = 'FAILED'
                    order.save(update_fields=['payment_status', 'updated_at'])

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ('provider', 'event_type', 'status', 'signature_verified', 'received_at')
    list_filter = ('provider', 'status', 'signature_verified')
    search_fields = ('event_id', 'error_summary')
    readonly_fields = [field.name for field in PaymentWebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CommissionLedger)
class CommissionLedgerAdmin(admin.ModelAdmin):
    list_display = ('order', 'commission_plan', 'commission_rate_bps', 'commission_amount_paise', 'gst_amount_paise', 'status', 'created_at')
    list_filter = ('status', 'commission_plan', 'created_at')
    readonly_fields = [field.name for field in CommissionLedger._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CommissionStatement)
class CommissionStatementAdmin(admin.ModelAdmin):
    list_display = ('statement_number', 'seller', 'period_start', 'commission_total_paise', 'gst_total_paise', 'status')
    list_filter = ('status', 'period_start')
    search_fields = ('statement_number', 'seller__username', 'seller__business_name')
    readonly_fields = ('statement_number', 'period_start', 'commission_total_paise', 'gst_total_paise', 'created_at')

    def save_model(self, request, obj, form, change):
        if obj.status == CommissionStatement.Status.ISSUED:
            if obj.gst_total_paise is None:
                raise ValidationError('Set the CA-confirmed GST rate and regenerate this statement before issuing it.')
            if obj.issued_at is None:
                obj.issued_at = timezone.now()
        if obj.status == CommissionStatement.Status.PAID:
            if obj.gst_total_paise is None:
                raise ValidationError('A statement with unconfigured GST cannot be marked paid.')
            if obj.issued_at is None:
                obj.issued_at = timezone.now()
            if obj.paid_at is None:
                obj.paid_at = timezone.now()
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if obj.status == CommissionStatement.Status.PAID:
                CommissionLedger.objects.filter(
                    statement=obj,
                    status=CommissionLedger.Status.INVOICED,
                ).update(status=CommissionLedger.Status.PAID)
