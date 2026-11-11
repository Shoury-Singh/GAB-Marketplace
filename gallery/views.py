import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import SuspiciousFileOperation
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.query import QuerySet
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.utils._os import safe_join
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from accounts.business_profiles import business_profile_for, business_profiles_for
from accounts.models import PincodeLocation, User
from .models import (
    GalleryImage,
    Order,
    OrderMessage,
    PaymentAttempt,
    PaymentWebhookEvent,
    Product,
    ProductInteraction,
    QuoteRequest,
    SellerBanner,
)
from .recommendations import distance_km, hash_search_query, rank_products
from .payment_services import (
    PaymentRecordingError,
    record_verified_provider_capture,
    record_verified_provider_failure,
    report_offline_payment,
)
from .razorpay_route import RazorpayRouteError, checkout_is_available, start_checkout
from .forms import (
    GalleryImageForm,
    OrderCreateForm,
    OrderMessageForm,
    OrderStatusForm,
    OfflinePaymentForm,
    ProductForm,
    QuoteRequestForm,
    QuoteResponseForm,
    SellerBannerForm,
)


SELLER_ROLES = {
    User.Role.INDUSTRY,
    User.Role.WHOLESALER,
    User.Role.SHOPKEEPER,
}
PURCHASED_ORDER_STATUSES = (
    Order.Status.ACCEPTED,
    Order.Status.PROCESSING,
    Order.Status.SHIPPED,
    Order.Status.DELIVERED,
)
DEMAND_CHART_COLORS = {
    Product.Category.FABRICS: '#4263eb',
    Product.Category.MACHINERY: '#12b886',
    Product.Category.PACKAGING: '#f59f00',
    Product.Category.CHEMICALS: '#e64980',
    Product.Category.FOOD: '#15aabf',
    Product.Category.ELECTRONICS: '#845ef7',
}
BUYER_SELLER_ROLES = {
    User.Role.BUYER: Product.SellerRole.SHOPKEEPER,
    User.Role.SHOPKEEPER: Product.SellerRole.WHOLESALER,
    User.Role.WHOLESALER: Product.SellerRole.INDUSTRY,
}
SELLER_BUYER_ROLES = {
    Product.SellerRole.INDUSTRY: User.Role.WHOLESALER,
    Product.SellerRole.WHOLESALER: User.Role.SHOPKEEPER,
    Product.SellerRole.SHOPKEEPER: User.Role.BUYER,
}
IMAGE_MANAGER_ROLES = {
    User.Role.WHOLESALER,
    User.Role.SHOPKEEPER,
}


def _record_interaction(request, event_type, product=None, category='', search_hash=''):
    if not request.user.is_authenticated:
        return
    ProductInteraction.objects.create(
        user=request.user,
        product=product,
        event_type=event_type,
        category=category,
        search_hash=search_hash,
    )


def serve_uploaded_media(request, media_path):
    try:
        file_path = Path(safe_join(settings.MEDIA_ROOT, media_path))
    except SuspiciousFileOperation as error:
        raise Http404 from error

    content_type, _ = mimetypes.guess_type(file_path.name)
    if not content_type or not content_type.startswith('image/'):
        raise Http404

    try:
        media_file = file_path.open('rb')
    except FileNotFoundError as error:
        raise Http404 from error

    response = FileResponse(media_file, content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'public, max-age=3600'
    return response


def _seller_required(request):
    if request.user.role not in SELLER_ROLES:
        messages.warning(request, 'Product management is only available to business sellers.')
        return False
    return True


def _attach_seller_profiles(items):
    is_queryset = isinstance(items, QuerySet)
    item_list = list(items)
    profiles = business_profiles_for(
        (item.owner_id, item.seller_role)
        for item in item_list
        if item.owner_id
    )
    for item in item_list:
        item.seller_profile = profiles.get((item.owner_id, item.seller_role))
    return items if is_queryset else item_list


def _attach_quote_profiles(quotes):
    quote_list = list(quotes)
    profile_pairs = set()
    for quote in quote_list:
        seller_role = quote.product.seller_role
        buyer_role = SELLER_BUYER_ROLES.get(seller_role)
        if quote.seller_id:
            profile_pairs.add((quote.seller_id, seller_role))
        if quote.buyer_id and buyer_role:
            profile_pairs.add((quote.buyer_id, buyer_role))

    profiles = business_profiles_for(profile_pairs)
    for quote in quote_list:
        seller_role = quote.product.seller_role
        buyer_role = SELLER_BUYER_ROLES.get(seller_role)
        quote.seller_profile = profiles.get((quote.seller_id, seller_role))
        quote.buyer_profile = (
            profiles.get((quote.buyer_id, buyer_role))
            if buyer_role
            else None
        )
    return quote_list


def _attach_order_profiles(orders):
    order_list = list(orders)
    profile_pairs = set()
    for order in order_list:
        seller_role = order.product.seller_role
        buyer_role = SELLER_BUYER_ROLES.get(seller_role)
        if order.seller_id:
            profile_pairs.add((order.seller_id, seller_role))
        if order.buyer_id and buyer_role:
            profile_pairs.add((order.buyer_id, buyer_role))

    profiles = business_profiles_for(profile_pairs)
    for order in order_list:
        seller_role = order.product.seller_role
        buyer_role = SELLER_BUYER_ROLES.get(seller_role)
        order.seller_profile = profiles.get((order.seller_id, seller_role))
        order.buyer_profile = (
            profiles.get((order.buyer_id, buyer_role))
            if buyer_role
            else None
        )
    return order_list


def _image_manager_required(request):
    if request.user.role not in IMAGE_MANAGER_ROLES:
        messages.warning(
            request,
            'Image management is only available to shopkeepers and wholesalers.',
        )
        return False
    return True


@login_required
def images_show(request):
    if not _image_manager_required(request):
        return redirect(request.user.get_dashboard_url())
    return redirect('my_images')


def catalog_view(request):
    category = request.GET.get('category', 'all')
    query = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'featured')
    if query:
        _record_interaction(
            request,
            ProductInteraction.EventType.SEARCH,
            category=category if category != 'all' else '',
            search_hash=hash_search_query(query[:200]),
        )

    buyer_role = getattr(request.user, 'role', User.Role.BUYER)
    seller_role = BUYER_SELLER_ROLES.get(buyer_role)
    products = (
        Product.objects.filter(
            is_active=True,
            seller_role=seller_role,
        ).select_related('owner')
        if seller_role
        else Product.objects.none()
    )

    location_message = ''
    buyer_location = None
    if sort == 'nearest' and buyer_role == User.Role.BUYER:
        if not request.user.is_authenticated:
            products = Product.objects.none()
            location_message = 'Sign in and add your PIN code in Settings to sort nearby shops.'
        else:
            buyer_profile = business_profile_for(request.user, User.Role.BUYER)
            buyer_pincode = (
                buyer_profile.pincode if buyer_profile and buyer_profile.pincode
                else request.user.pincode
            )
            if not buyer_pincode:
                products = Product.objects.none()
                location_message = 'Add your six-digit PIN code in Settings to sort nearby shops.'
            else:
                buyer_location = PincodeLocation.objects.filter(
                    pincode=buyer_pincode,
                ).first()
                if buyer_location is None:
                    products = Product.objects.none()
                    location_message = (
                        'Your PIN code is not available in the local postal map data yet.'
                    )
                else:
                    products = products.filter(
                        inventory__gte=F('minimum_order'),
                        owner__is_active=True,
                    )

    if category != 'all':
        products = products.filter(category=category)

    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(supplier__icontains=query)
            | Q(location__icontains=query)
            | Q(owner__business_name__icontains=query)
            | Q(owner__city__icontains=query)
            | Q(owner__state__icontains=query)
            | Q(owner__role_assignments__role=seller_role,
                owner__role_assignments__business_name__icontains=query)
            | Q(owner__role_assignments__role=seller_role,
                owner__role_assignments__city__icontains=query)
            | Q(owner__role_assignments__role=seller_role,
                owner__role_assignments__state__icontains=query)
        )

    if sort == 'nearest' and buyer_location is not None:
        product_list = list(products)
        product_profiles = business_profiles_for(
            (product.owner_id, product.seller_role)
            for product in product_list
            if product.owner_id
        )
        seller_pincodes = {
            profile.pincode
            for profile in product_profiles.values()
            if profile.pincode
        }
        seller_pincodes.update(
            product.owner.pincode
            for product in product_list
            if product.owner and product.owner.pincode
        )
        seller_locations = PincodeLocation.objects.in_bulk(
            seller_pincodes,
            field_name='pincode',
        )
        located_products = []
        for product in product_list:
            seller_profile = product_profiles.get(
                (product.owner_id, product.seller_role),
            )
            seller_location = seller_locations.get(
                (
                    seller_profile.pincode
                    if seller_profile and seller_profile.pincode
                    else product.owner.pincode if product.owner else None
                ),
            )
            if seller_location is None:
                continue
            product.seller_profile = seller_profile
            product.distance_km = round(
                distance_km(buyer_location, seller_location),
                1,
            )
            located_products.append(product)
        products = sorted(located_products, key=lambda product: product.distance_km)
        if products and len(products) != len(product_list):
            location_message = (
                'Distances use approximate PIN-code centers; shops without a mapped '
                'PIN code are omitted.'
            )
        elif not products:
            location_message = (
                'No in-stock shop products with mapped PIN codes match this search.'
            )
    elif sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'rating':
        products = products.order_by('-rating', '-review_count')
    else:
        products = rank_products(products, request.user)

    products = _attach_seller_profiles(products)
    banners = _attach_seller_profiles(
        SellerBanner.objects.filter(
            is_active=True,
            owner__is_active=True,
            seller_role=seller_role,
        ).select_related('owner')[:4]
    )
    category_counts = {}
    if seller_role:
        category_counts = {
            row['category']: row['total']
            for row in Product.objects.filter(
                is_active=True,
                seller_role=seller_role,
            ).values('category').annotate(total=Count('id'))
        }
    categories = [choice[0] for choice in Product.Category.choices]
    context = {
        'products': products,
        'banners': banners,
        'categories': categories,
        'category_options': [
            {'value': category, 'count': category_counts.get(category, 0)}
            for category in categories
        ],
        'selected_category': category,
        'query': query,
        'sort': sort,
        'total_products': (
            len(products) if isinstance(products, list) else products.count()
        ),
        'location_message': location_message,
        'page_title': 'Catalog - GAB Platform',
    }
    return render(request, 'gallery/catalog.html', context)


def shops_view(request):
    shops = User.objects.filter(is_active=True).filter(
        Q(role=User.Role.SHOPKEEPER)
        | Q(role_assignments__role=User.Role.SHOPKEEPER)
    ).distinct().annotate(
        active_product_count=Count(
            'products',
            filter=Q(
                products__is_active=True,
                products__seller_role=Product.SellerRole.SHOPKEEPER,
            ),
            distinct=True,
        ),
    ).order_by('business_name', 'username')
    shops = list(shops)
    profiles = business_profiles_for(
        (shop.pk, User.Role.SHOPKEEPER) for shop in shops
    )
    for shop in shops:
        shop.seller_profile = profiles.get((shop.pk, User.Role.SHOPKEEPER))
    return render(request, 'gallery/shops.html', {
        'shops': shops,
        'page_title': 'Explore Shops - GAB',
    })


def shop_detail(request, pk):
    shop = get_object_or_404(
        User.objects.filter(is_active=True).filter(
            Q(role=User.Role.SHOPKEEPER)
            | Q(role_assignments__role=User.Role.SHOPKEEPER)
        ).distinct(),
        pk=pk,
    )
    products = Product.objects.filter(
        owner=shop,
        seller_role=Product.SellerRole.SHOPKEEPER,
        is_active=True,
    )
    products = _attach_seller_profiles(products)
    shop.seller_profile = business_profile_for(shop, User.Role.SHOPKEEPER)
    return render(request, 'gallery/shop_detail.html', {
        'shop': shop,
        'products': products,
        'page_title': (
            f'{shop.seller_profile.business_name or shop.username} - GAB Shop'
            if shop.seller_profile
            else f'{shop.username} - GAB Shop'
        ),
    })


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('owner'),
        slug=slug,
        is_active=True,
    )
    _attach_seller_profiles([product])
    buyer_role = getattr(request.user, 'role', User.Role.BUYER)
    allowed_seller_role = BUYER_SELLER_ROLES.get(buyer_role)
    if product.owner_id != getattr(request.user, 'pk', None) and (
        allowed_seller_role is None
        or product.seller_role != allowed_seller_role
    ):
        raise Http404
    if request.user.is_authenticated and product.owner_id != request.user.pk:
        if request.GET.get('source') == 'recommendation':
            _record_interaction(
                request,
                ProductInteraction.EventType.CLICK,
                product=product,
                category=product.category,
            )
        _record_interaction(
            request,
            ProductInteraction.EventType.PRODUCT_VIEW,
            product=product,
            category=product.category,
        )
    expected_buyer_role = SELLER_BUYER_ROLES.get(product.seller_role)
    can_contact_seller = (
        product.seller_role == allowed_seller_role
        and (
            not request.user.is_authenticated
            or request.user.role == expected_buyer_role
        )
    )
    related_products = Product.objects.filter(
        is_active=True,
        category=product.category,
        seller_role=product.seller_role,
    ).exclude(pk=product.pk)[:4]
    related_products = _attach_seller_profiles(related_products)
    return render(request, 'gallery/product_detail.html', {
        'product': product,
        'related_products': related_products,
        'can_contact_seller': can_contact_seller,
        'page_title': f'{product.name} - GAB Platform',
    })


@login_required
def my_products(request):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    purchased_orders = Q(
        orders__seller=request.user,
        orders__status__in=PURCHASED_ORDER_STATUSES,
    )
    products = list(
        Product.objects.filter(owner=request.user).annotate(
            buyer_count=Count(
                'orders__buyer',
                filter=purchased_orders,
                distinct=True,
            ),
            order_count=Count(
                'orders',
                filter=purchased_orders,
                distinct=True,
            ),
            units_sold=Sum('orders__quantity', filter=purchased_orders),
        )
    )
    demand = Product.objects.filter(owner=request.user).values(
        'category',
    ).annotate(
        units_sold=Sum(
            'orders__quantity',
            filter=Q(
                orders__seller=request.user,
                orders__status__in=PURCHASED_ORDER_STATUSES,
            ),
        ),
    )
    units_by_category = {
        row['category']: row['units_sold'] or 0
        for row in demand
    }
    category_labels = dict(Product.Category.choices)
    total_units_sold = sum(units_by_category.values())
    demand_categories = []
    gradient_segments = []
    degrees = 0
    for category, label in Product.Category.choices:
        units_sold = units_by_category.get(category, 0)
        if not units_sold:
            continue
        end_degrees = (
            360
            if units_sold == total_units_sold
            else degrees + (360 * units_sold / total_units_sold)
        )
        color = DEMAND_CHART_COLORS[category]
        gradient_segments.append(
            f'{color} {degrees:.2f}deg {end_degrees:.2f}deg'
        )
        demand_categories.append({
            'category': category,
            'label': category_labels[category],
            'units_sold': units_sold,
            'percentage': round(units_sold * 100 / total_units_sold),
            'color': color,
        })
        degrees = end_degrees
    sales_totals = Order.objects.filter(
        seller=request.user,
        status__in=PURCHASED_ORDER_STATUSES,
    ).aggregate(
        buyer_count=Count('buyer', distinct=True),
        units_sold=Sum('quantity'),
    )
    return render(request, 'gallery/my_products.html', {
        'products': products,
        'active_product_count': sum(product.is_active for product in products),
        'buyer_count': sales_totals['buyer_count'],
        'units_sold': sales_totals['units_sold'] or 0,
        'demand_categories': demand_categories,
        'demand_chart_gradient': ', '.join(gradient_segments),
        'page_title': 'My Products - GAB',
    })


@login_required
def my_banners(request):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    return render(request, 'gallery/my_banners.html', {
        'banners': SellerBanner.objects.filter(owner=request.user),
        'page_title': 'My Advertisements - GAB',
    })


@login_required
def create_banner(request):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    if request.method == 'POST':
        form = SellerBannerForm(request.POST, request.FILES)
        if form.is_valid():
            banner = form.save(commit=False)
            banner.owner = request.user
            banner.save()
            messages.success(request, 'Your banner is now live in the marketplace.')
            return redirect('my_banners')
    else:
        form = SellerBannerForm()
    return render(request, 'gallery/banner_form.html', {
        'form': form,
        'page_title': 'Advertise Your Business - GAB',
    })


@login_required
def edit_banner(request, pk):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    banner = get_object_or_404(SellerBanner, pk=pk, owner=request.user)
    if request.method == 'POST':
        form = SellerBannerForm(request.POST, request.FILES, instance=banner)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your banner was updated.')
            return redirect('my_banners')
    else:
        form = SellerBannerForm(instance=banner)
    return render(request, 'gallery/banner_form.html', {
        'form': form,
        'banner': banner,
        'page_title': 'Edit Advertisement - GAB',
    })


@login_required
@require_POST
def toggle_banner(request, pk):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    banner = get_object_or_404(SellerBanner, pk=pk, owner=request.user)
    banner.is_active = not banner.is_active
    banner.save(update_fields=['is_active', 'updated_at'])
    state = 'published' if banner.is_active else 'paused'
    messages.success(request, f'Your banner is now {state}.')
    return redirect('my_banners')


@login_required
def create_product(request):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save(commit=False)
            product.owner = request.user
            profile = business_profile_for(request.user, request.user.role)
            product.supplier = (
                (profile.business_name if profile else '')
                or request.user.business_name
                or request.user.username
            )[:120]
            city_state = ', '.join(filter(
                None,
                [
                    (profile.city if profile else '') or request.user.city,
                    (profile.state if profile else '') or request.user.state,
                ],
            ))
            product.location = (city_state or 'India')[:120]
            product.save()
            messages.success(request, f'{product.name} is now listed in the catalog.')
            return redirect('my_products')
    else:
        form = ProductForm()
    return render(request, 'gallery/product_form.html', {
        'form': form,
        'page_title': 'Add Product - GAB',
    })


@login_required
def edit_product(request, pk):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    product = get_object_or_404(Product, pk=pk, owner=request.user)
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f'{product.name} was updated.')
            return redirect('my_products')
    else:
        form = ProductForm(instance=product)
    return render(request, 'gallery/product_form.html', {
        'form': form,
        'product': product,
        'page_title': f'Edit {product.name} - GAB',
    })


@login_required
@require_POST
def toggle_product(request, pk):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    product = get_object_or_404(Product, pk=pk, owner=request.user)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active', 'updated_at'])
    state = 'published' if product.is_active else 'hidden'
    messages.success(request, f'{product.name} is now {state}.')
    return redirect('my_products')


@login_required
def request_quote(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('owner'),
        slug=slug,
        is_active=True,
    )
    _attach_seller_profiles([product])
    if product.owner is None:
        messages.error(request, 'This listing has no seller account to receive a quote request.')
        return redirect('product_detail', slug=product.slug)
    if product.owner_id == request.user.pk:
        messages.warning(request, 'You cannot request a quote for your own product.')
        return redirect('product_detail', slug=product.slug)
    allowed_seller_role = BUYER_SELLER_ROLES.get(request.user.role)
    if product.seller_role != allowed_seller_role:
        messages.warning(
            request,
            'This listing is not available to your current account role.',
        )
        return redirect('catalog')

    if request.method == 'POST':
        form = QuoteRequestForm(request.POST)
        if form.is_valid():
            if form.cleaned_data['quantity'] < product.minimum_order:
                form.add_error(
                    'quantity',
                    f'The minimum order for this product is {product.minimum_order}.',
                )
            else:
                quote = form.save(commit=False)
                quote.product = product
                quote.buyer = request.user
                quote.seller = product.owner
                quote.save()
                _record_interaction(
                    request,
                    ProductInteraction.EventType.QUOTE_REQUEST,
                    product=product,
                    category=product.category,
                )
                messages.success(request, 'Your quote request was sent to the seller.')
                return redirect('buyer_quotes')
    else:
        form = QuoteRequestForm()
    return render(request, 'gallery/request_quote.html', {
        'form': form,
        'product': product,
        'seller_profile': product.seller_profile,
        'page_title': f'Request a Quote - {product.name}',
    })


@login_required
def buyer_quotes(request):
    quotes = QuoteRequest.objects.filter(
        buyer=request.user,
    ).select_related('product', 'seller')
    quotes = _attach_quote_profiles(quotes)
    return render(request, 'gallery/buyer_quotes.html', {
        'quotes': quotes,
        'page_title': 'My Quote Requests - GAB',
    })


@login_required
def seller_quotes(request):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    quotes = QuoteRequest.objects.filter(
        seller=request.user,
    ).select_related('product', 'buyer')
    quotes = _attach_quote_profiles(quotes)
    return render(request, 'gallery/seller_quotes.html', {
        'quotes': quotes,
        'page_title': 'Incoming Quote Requests - GAB',
    })


@login_required
@require_POST
def respond_to_quote(request, pk):
    if not _seller_required(request):
        return redirect(request.user.get_dashboard_url())
    quote = get_object_or_404(QuoteRequest, pk=pk, seller=request.user)
    if quote.status != QuoteRequest.Status.PENDING:
        messages.warning(request, 'This quote request has already been answered.')
        return redirect('seller_quotes')
    form = QuoteResponseForm(request.POST, instance=quote)
    if form.is_valid():
        form.save()
        messages.success(request, 'Your response was sent to the buyer.')
    else:
        messages.error(
            request,
            'Choose a valid response and enter a price per unit when sending a quote.',
        )
    return redirect('seller_quotes')


@login_required
@require_POST
def accept_quote(request, pk):
    quote = get_object_or_404(
        QuoteRequest.objects.select_related('product', 'seller'),
        pk=pk,
        buyer=request.user,
    )
    with transaction.atomic():
        quote = QuoteRequest.objects.select_for_update().select_related(
            'product', 'seller',
        ).get(pk=quote.pk)
        existing_order = Order.objects.filter(source_quote=quote).first()
        if existing_order:
            return redirect('order_detail', pk=existing_order.pk)
        if (
            quote.status != QuoteRequest.Status.QUOTED
            or quote.quoted_unit_price is None
        ):
            messages.error(request, 'This request does not have an active seller quote.')
            return redirect('buyer_quotes')

        product = Product.objects.select_for_update().get(pk=quote.product_id)
        if product.inventory < quote.quantity:
            messages.error(
                request,
                'The quoted quantity is no longer available. Contact the seller for an update.',
            )
            return redirect('buyer_quotes')

        order = Order.objects.create(
            product=product,
            buyer=quote.buyer,
            seller=quote.seller,
            source_quote=quote,
            quantity=quote.quantity,
            unit_price=quote.quoted_unit_price,
            notes=quote.message,
            status=Order.Status.PENDING,
        )
        quote.status = QuoteRequest.Status.ACCEPTED
        quote.save(update_fields=['status', 'updated_at'])

    messages.success(request, 'You accepted the quote and sent an order request to the supplier.')
    return redirect('order_detail', pk=order.pk)


def _allowed_order_statuses(order, user):
    if order.seller_id == user.pk:
        transitions = {
            Order.Status.PENDING: [Order.Status.ACCEPTED, Order.Status.DECLINED],
            Order.Status.ACCEPTED: [Order.Status.PROCESSING, Order.Status.CANCELLED],
            Order.Status.PROCESSING: [Order.Status.SHIPPED, Order.Status.CANCELLED],
            Order.Status.SHIPPED: [Order.Status.DELIVERED],
        }
    else:
        return []
    return transitions.get(order.status, [])


def _get_participant_order(request, pk):
    return get_object_or_404(
        Order.objects.select_related('product', 'buyer', 'seller').filter(
            Q(buyer=request.user) | Q(seller=request.user)
        ),
        pk=pk,
    )


def _serialize_order_messages(order, order_messages, current_user):
    profile_pairs = set()
    buyer_role = SELLER_BUYER_ROLES.get(order.product.seller_role)
    for message in order_messages:
        if message.sender_id == order.seller_id:
            sender_role = order.product.seller_role
        else:
            sender_role = buyer_role
        if message.sender_id and sender_role:
            profile_pairs.add((message.sender_id, sender_role))

    profiles = business_profiles_for(profile_pairs)
    serialized = []
    for message in order_messages:
        sender_role = (
            order.product.seller_role
            if message.sender_id == order.seller_id
            else buyer_role
        )
        sender_profile = profiles.get((message.sender_id, sender_role))
        sender_name = (
            (sender_profile.business_name if sender_profile else '')
            or message.sender.business_name
            or message.sender.get_full_name()
            or message.sender.username
        )
        serialized.append({
            'id': message.pk,
            'sender_id': message.sender_id,
            'sender_name': sender_name,
            'body': message.body,
            'created_at': message.created_at.isoformat(),
            'is_mine': message.sender_id == current_user.pk,
        })
    return serialized


@login_required
def create_order(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('owner'),
        slug=slug,
        is_active=True,
    )
    _attach_seller_profiles([product])
    if product.owner is None:
        messages.error(request, 'This listing has no supplier account to receive an order.')
        return redirect('product_detail', slug=product.slug)
    required_buyer_role = SELLER_BUYER_ROLES.get(product.seller_role)
    if request.user.role != required_buyer_role:
        messages.warning(
            request,
            'This product is only available to the next business level in the supply chain.',
        )
        return redirect(request.user.get_dashboard_url())
    if product.owner_id == request.user.pk:
        messages.warning(request, 'You cannot place an order for your own product.')
        return redirect('product_detail', slug=product.slug)

    if request.method == 'POST':
        form = OrderCreateForm(request.POST, product=product)
        if form.is_valid():
            order = Order.objects.create(
                product=product,
                buyer=request.user,
                seller=product.owner,
                quantity=form.cleaned_data['quantity'],
                notes=form.cleaned_data['notes'],
            )
            messages.success(request, 'Your order request was sent to the supplier.')
            return redirect('order_detail', pk=order.pk)
    else:
        form = OrderCreateForm(product=product)

    return render(request, 'gallery/order_form.html', {
        'form': form,
        'product': product,
        'seller_profile': product.seller_profile,
        'page_title': f'Place Order - {product.name}',
    })


@login_required
def my_orders(request):
    if request.user.role == User.Role.INDUSTRY:
        orders = Order.objects.filter(seller=request.user)
        heading = 'Incoming Orders'
    elif request.user.role in {
        User.Role.WHOLESALER,
        User.Role.SHOPKEEPER,
    }:
        orders = Order.objects.filter(
            Q(seller=request.user) | Q(buyer=request.user)
        )
        heading = 'My Orders'
    elif request.user.is_buyer:
        orders = Order.objects.filter(buyer=request.user)
        heading = 'My Orders'
    else:
        messages.warning(request, 'Orders are available to buyer and supplier accounts.')
        return redirect(request.user.get_dashboard_url())

    orders = orders.select_related('product', 'buyer', 'seller').annotate(
        message_count=Count('messages'),
    )
    orders = _attach_order_profiles(orders)
    return render(request, 'gallery/my_orders.html', {
        'orders': orders,
        'heading': heading,
        'page_title': f'{heading} - GAB',
    })


@login_required
@require_POST
def set_shop_status(request):
    if not request.user.is_shopkeeper:
        messages.warning(request, 'Only shopkeepers can change shop availability.')
        return redirect(request.user.get_dashboard_url())

    status = request.POST.get('status')
    if status not in {'open', 'closed'}:
        messages.error(request, 'Choose whether to open or close your shop.')
        return redirect(request.user.get_dashboard_url())

    request.user.shop_is_open = status == 'open'
    request.user.shop_status_changed_at = timezone.now()
    request.user.save(update_fields=[
        'shop_is_open',
        'shop_status_changed_at',
        'updated_at',
    ])
    state = 'open' if request.user.shop_is_open else 'closed'
    messages.success(request, f'Your shop is now {state}. Customers can still send order requests while it is closed.')
    return redirect(request.user.get_dashboard_url())


@login_required
def order_detail(request, pk):
    order = _get_participant_order(request, pk)
    order = _attach_order_profiles([order])[0]
    order_messages = list(order.messages.select_related('sender'))
    message_profiles = business_profiles_for(
        (
            message.sender_id,
            (
                order.product.seller_role
                if message.sender_id == order.seller_id
                else SELLER_BUYER_ROLES.get(order.product.seller_role)
            ),
        )
        for message in order_messages
        if message.sender_id
    )
    for message in order_messages:
        sender_role = (
            order.product.seller_role
            if message.sender_id == order.seller_id
            else SELLER_BUYER_ROLES.get(order.product.seller_role)
        )
        message.sender_profile = message_profiles.get(
            (message.sender_id, sender_role),
        )
    allowed_statuses = _allowed_order_statuses(order, request.user)
    status_form = (
        OrderStatusForm(allowed_statuses=allowed_statuses)
        if allowed_statuses else None
    )
    offline_payment_form = (
        OfflinePaymentForm()
        if order.seller_id == request.user.pk
        and order.payment_status in {'UNPAID', 'FAILED'}
        and order.status not in {Order.Status.PENDING, Order.Status.DECLINED, Order.Status.CANCELLED}
        else None
    )
    payment_attempts = order.payment_attempts.select_related('recorded_by').all()
    return render(request, 'gallery/order_detail.html', {
        'order': order,
        'payment_attempts': payment_attempts,
        'online_payment_available': (
            order.buyer_id == request.user.pk and checkout_is_available(order)
        ),
        'offline_payment_form': offline_payment_form,
        'order_messages': order_messages,
        'last_message_id': max((message.pk for message in order_messages), default=0),
        'message_form': OrderMessageForm(),
        'status_form': status_form,
        'is_order_supplier': order.seller_id == request.user.pk,
        'page_title': f'Order #{order.pk} - GAB',
    })


@login_required
@require_POST
def start_order_payment(request, pk):
    order = _get_participant_order(request, pk)
    try:
        checkout = start_checkout(order_id=order.pk, buyer_id=request.user.pk)
    except RazorpayRouteError as error:
        return JsonResponse({'ok': False, 'error': str(error)}, status=409)
    return JsonResponse({
        'ok': True,
        **checkout,
        'name': 'GAB Platform',
        'description': f'Order #{order.pk} · {order.product_name}',
    })


@login_required
@require_GET
def order_payment_status(request, pk):
    order = _get_participant_order(request, pk)
    return JsonResponse({
        'ok': True,
        'payment_status': order.payment_status,
        'payment_status_display': order.get_payment_status_display(),
        'paid_at': order.paid_at.isoformat() if order.paid_at else None,
    })


@csrf_exempt
@require_POST
def razorpay_route_webhook(request):
    webhook_secret = os.environ.get('GAB_RAZORPAY_WEBHOOK_SECRET', '')
    signature = request.headers.get('X-Razorpay-Signature', '')
    event_id = request.headers.get('X-Razorpay-Event-Id', '').strip()
    if not webhook_secret:
        return HttpResponse(status=503)
    expected = hmac.new(
        webhook_secret.encode('utf-8'),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        return HttpResponseBadRequest('Invalid webhook signature.')
    if not event_id or len(event_id) > 160:
        return HttpResponseBadRequest('Missing provider event ID.')
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponseBadRequest('Invalid webhook payload.')

    if not isinstance(payload, dict):
        return HttpResponseBadRequest('Invalid webhook payload.')
    event_type = payload.get('event', '')
    if not isinstance(event_type, str):
        return HttpResponseBadRequest('Invalid webhook event type.')
    event_payload = payload.get('payload')
    payment_payload = event_payload.get('payment') if isinstance(event_payload, dict) else None
    entity = payment_payload.get('entity') if isinstance(payment_payload, dict) else None
    if event_type not in {'payment.captured', 'payment.failed'} or not isinstance(entity, dict):
        PaymentWebhookEvent.objects.get_or_create(
            provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
            event_id=event_id,
            defaults={
                'event_type': event_type[:100] or 'unknown',
                'payload_hash': hashlib.sha256(request.body).hexdigest(),
                'safe_payload': {},
                'signature_verified': True,
                'status': PaymentWebhookEvent.Status.IGNORED,
            },
        )
        return HttpResponse(status=200)

    provider_order_id = str(entity.get('order_id') or '')
    attempt = PaymentAttempt.objects.filter(
        provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
        provider_order_id=provider_order_id,
    ).first()
    safe_payload = {
        'order_id': provider_order_id,
        'payment_id': str(entity.get('id') or ''),
        'amount': entity.get('amount'),
        'currency': str(entity.get('currency') or ''),
        'status': str(entity.get('status') or ''),
    }
    digest = hashlib.sha256(request.body).hexdigest()
    if not attempt:
        PaymentWebhookEvent.objects.get_or_create(
            provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
            event_id=event_id,
            defaults={
                'event_type': event_type,
                'payload_hash': digest,
                'safe_payload': safe_payload,
                'signature_verified': True,
                'status': PaymentWebhookEvent.Status.FAILED,
                'error_summary': 'Provider order is not linked to a local attempt; reconcile manually.',
            },
        )
        return HttpResponse(status=200)

    if event_type == 'payment.captured':
        if entity.get('status') != 'captured':
            PaymentWebhookEvent.objects.get_or_create(
                provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
                event_id=event_id,
                defaults={
                    'event_type': event_type,
                    'payload_hash': digest,
                    'safe_payload': safe_payload,
                    'signature_verified': True,
                    'status': PaymentWebhookEvent.Status.FAILED,
                    'error_summary': 'Capture event did not contain a captured payment status.',
                },
            )
            return HttpResponse(status=200)
        try:
            amount_paise = int(entity.get('amount'))
        except (TypeError, ValueError):
            amount_paise = 0
        payment_id = str(entity.get('id') or '')
        currency = str(entity.get('currency') or '')
        if not payment_id or amount_paise <= 0 or not currency:
            PaymentWebhookEvent.objects.get_or_create(
                provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
                event_id=event_id,
                defaults={
                    'event_type': event_type,
                    'payload_hash': digest,
                    'safe_payload': safe_payload,
                    'signature_verified': True,
                    'status': PaymentWebhookEvent.Status.FAILED,
                    'error_summary': 'Captured event is missing payment ID, amount, or currency.',
                },
            )
            return HttpResponse(status=200)
        record_verified_provider_capture(
            provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
            event_id=event_id,
            event_type=event_type,
            attempt_id=attempt.pk,
            provider_order_id=provider_order_id,
            provider_payment_id=payment_id,
            amount_paise=amount_paise,
            currency=currency,
            payload_hash=digest,
            safe_payload=safe_payload,
        )
    else:
        record_verified_provider_failure(
            provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
            event_id=event_id,
            event_type=event_type,
            attempt_id=attempt.pk,
            provider_order_id=provider_order_id,
            provider_payment_id=str(entity.get('id') or ''),
            failure_code=str(entity.get('error_code') or ''),
            payload_hash=digest,
            safe_payload=safe_payload,
        )
    return HttpResponse(status=200)


@login_required
@require_POST
def report_order_offline_payment(request, pk):
    order = _get_participant_order(request, pk)
    if order.seller_id != request.user.pk:
        messages.error(request, 'Only the seller can report an offline payment.')
        return redirect('order_detail', pk=order.pk)
    form = OfflinePaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Choose a valid offline payment method.')
        return redirect('order_detail', pk=order.pk)
    try:
        report_offline_payment(
            order_id=order.pk,
            seller_id=request.user.pk,
            method=form.cleaned_data['method'],
            reference=form.cleaned_data['reference'],
        )
    except PaymentRecordingError as error:
        messages.error(request, str(error))
        return redirect('order_detail', pk=order.pk)
    messages.success(
        request,
        'Offline payment recorded as seller-reported; GAB has not independently verified it.',
    )
    return redirect('order_detail', pk=order.pk)


@login_required
@require_POST
def send_order_message(request, pk):
    order = _get_participant_order(request, pk)
    form = OrderMessageForm(request.POST)
    if form.is_valid():
        message = OrderMessage.objects.create(
            order=order,
            sender=request.user,
            body=form.cleaned_data['body'],
        )
        if 'application/json' in request.headers.get('Accept', ''):
            payload = _serialize_order_messages(order, [message], request.user)[0]
            return JsonResponse({'ok': True, 'message': payload}, status=201)
    else:
        if 'application/json' in request.headers.get('Accept', ''):
            return JsonResponse(
                {'ok': False, 'errors': form.errors.get_json_data()},
                status=400,
            )
        messages.error(request, 'Messages must contain up to 2,000 characters.')
    return redirect('order_detail', pk=order.pk)


@login_required
@require_GET
def order_messages_feed(request, pk):
    order = _get_participant_order(request, pk)
    try:
        after_id = int(request.GET.get('after', '0'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid message cursor.'}, status=400)
    if after_id < 0:
        return JsonResponse({'ok': False, 'error': 'Invalid message cursor.'}, status=400)

    new_messages = list(
        OrderMessage.objects.filter(order=order, pk__gt=after_id)
        .select_related('sender')
        .order_by('pk')[:100]
    )
    return JsonResponse({
        'ok': True,
        'messages': _serialize_order_messages(order, new_messages, request.user),
    })


@login_required
@require_POST
def update_order_status(request, pk):
    order = _get_participant_order(request, pk)
    allowed_statuses = _allowed_order_statuses(order, request.user)
    form = OrderStatusForm(
        request.POST,
        allowed_statuses=allowed_statuses,
    )
    if not allowed_statuses or not form.is_valid():
        messages.error(request, 'That order status change is not allowed.')
        return redirect('order_detail', pk=order.pk)

    new_status = form.cleaned_data['status']
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if new_status not in _allowed_order_statuses(order, request.user):
            messages.error(request, 'That order status change is no longer allowed.')
            return redirect('order_detail', pk=order.pk)

        if new_status == Order.Status.ACCEPTED:
            product = Product.objects.select_for_update().get(pk=order.product_id)
            if product.inventory < order.quantity:
                messages.error(request, 'There is not enough inventory to accept this order.')
                return redirect('order_detail', pk=order.pk)
            product.inventory -= order.quantity
            product.save(update_fields=['inventory', 'updated_at'])
        elif (
            new_status == Order.Status.CANCELLED
            and order.status in {Order.Status.ACCEPTED, Order.Status.PROCESSING}
        ):
            product = Product.objects.select_for_update().get(pk=order.product_id)
            product.inventory += order.quantity
            product.save(update_fields=['inventory', 'updated_at'])

        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])

    messages.success(request, f'Order status updated to {order.get_status_display()}.')
    return redirect('order_detail', pk=order.pk)


@login_required
def upload_image(request):
    if not _image_manager_required(request):
        return redirect(request.user.get_dashboard_url())
    if request.method == 'POST':
        form = GalleryImageForm(request.POST, request.FILES)
        if form.is_valid():
            img = form.save(commit=False)
            img.uploaded_by = request.user
            img.save()
            messages.success(
                request,
                f'Image "{img.title}" uploaded successfully.'
            )
            return redirect('my_images')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = GalleryImageForm()

    context = {
        'form': form,
        'page_title': 'Upload Image - GAB Platform'
    }
    return render(request, 'gallery/upload_image.html', context)


@login_required
def my_images(request):
    if not _image_manager_required(request):
        return redirect(request.user.get_dashboard_url())
    my_imgs = GalleryImage.objects.filter(uploaded_by=request.user)
    context = {
        'images': my_imgs,
        'page_title': 'My Images - GAB Platform'
    }
    return render(request, 'gallery/my_images.html', context)


@login_required
def edit_image(request, pk):
    if not _image_manager_required(request):
        return redirect(request.user.get_dashboard_url())
    image = get_object_or_404(GalleryImage, pk=pk, uploaded_by=request.user)
    if request.method == 'POST':
        form = GalleryImageForm(request.POST, request.FILES, instance=image)
        if form.is_valid():
            form.save()
            messages.success(request, f'Image "{image.title}" was updated.')
            return redirect('my_images')
    else:
        form = GalleryImageForm(instance=image)
    return render(request, 'gallery/upload_image.html', {
        'form': form,
        'image': image,
        'page_title': f'Edit {image.title} - GAB Platform',
    })


@login_required
@require_POST
def delete_image(request, pk):
    img = get_object_or_404(GalleryImage, pk=pk, uploaded_by=request.user)
    if not _image_manager_required(request):
        return redirect(request.user.get_dashboard_url())
    title = img.title
    img.delete()
    messages.success(request, f'Image "{title}" deleted successfully.')
    return redirect('my_images')
