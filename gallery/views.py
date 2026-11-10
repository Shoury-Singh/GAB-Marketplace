import mimetypes
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import SuspiciousFileOperation
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils._os import safe_join
from django.views.decorators.http import require_POST
from accounts.models import PincodeLocation, User
from .models import (
    GalleryImage,
    Order,
    OrderMessage,
    Product,
    QuoteRequest,
    SellerBanner,
)
from .forms import (
    GalleryImageForm,
    OrderCreateForm,
    OrderMessageForm,
    OrderStatusForm,
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


def _distance_km(origin, destination):
    latitude_delta = radians(float(destination.latitude) - float(origin.latitude))
    longitude_delta = radians(float(destination.longitude) - float(origin.longitude))
    origin_latitude = radians(float(origin.latitude))
    destination_latitude = radians(float(destination.latitude))
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(origin_latitude)
        * cos(destination_latitude)
        * sin(longitude_delta / 2) ** 2
    )
    return 6371 * 2 * atan2(sqrt(haversine), sqrt(1 - haversine))


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


def images_show(request):
    """
    Public Images Show Page.
    Displays all active gallery images in a responsive grid.
    """
    images_list = GalleryImage.objects.filter(is_active=True).select_related('uploaded_by')

    paginator = Paginator(images_list, 12)  # 12 images per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'images': page_obj,
        'total_images': images_list.count(),
        'page_title': 'Images Gallery - GAB Platform'
    }
    return render(request, 'gallery/images_show.html', context)


def catalog_view(request):
    category = request.GET.get('category', 'all')
    query = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'featured')

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
        elif not request.user.pincode:
            products = Product.objects.none()
            location_message = 'Add your six-digit PIN code in Settings to sort nearby shops.'
        else:
            buyer_location = PincodeLocation.objects.filter(
                pincode=request.user.pincode,
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
        )

    if sort == 'nearest' and buyer_location is not None:
        product_list = list(products)
        seller_pincodes = {
            product.owner.pincode
            for product in product_list
            if product.owner and product.owner.pincode
        }
        seller_locations = PincodeLocation.objects.in_bulk(
            seller_pincodes,
            field_name='pincode',
        )
        located_products = []
        for product in product_list:
            seller_location = seller_locations.get(
                product.owner.pincode if product.owner else None,
            )
            if seller_location is None:
                continue
            product.distance_km = round(
                _distance_km(buyer_location, seller_location),
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
        products = products.order_by('-is_featured', '-created_at')

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
        'banners': SellerBanner.objects.filter(
            is_active=True,
            owner__is_active=True,
            seller_role=seller_role,
        ).select_related('owner')[:4],
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
    ).annotate(
        active_product_count=Count(
            'products',
            filter=Q(
                products__is_active=True,
                products__seller_role=Product.SellerRole.SHOPKEEPER,
            ),
            distinct=True,
        ),
    ).order_by('business_name', 'username')
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
    return render(request, 'gallery/shop_detail.html', {
        'shop': shop,
        'products': products,
        'page_title': f'{shop.business_name or shop.username} - GAB Shop',
    })


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('owner'),
        slug=slug,
        is_active=True,
    )
    buyer_role = getattr(request.user, 'role', User.Role.BUYER)
    allowed_seller_role = BUYER_SELLER_ROLES.get(buyer_role)
    if product.owner_id != getattr(request.user, 'pk', None) and (
        allowed_seller_role is None
        or product.seller_role != allowed_seller_role
    ):
        raise Http404
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
    products = Product.objects.filter(owner=request.user)
    return render(request, 'gallery/my_products.html', {
        'products': products,
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
            product.supplier = request.user.business_name or request.user.username
            city_state = ', '.join(filter(None, [request.user.city, request.user.state]))
            product.location = city_state or 'India'
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
                messages.success(request, 'Your quote request was sent to the seller.')
                return redirect('buyer_quotes')
    else:
        form = QuoteRequestForm()
    return render(request, 'gallery/request_quote.html', {
        'form': form,
        'product': product,
        'page_title': f'Request a Quote - {product.name}',
    })


@login_required
def buyer_quotes(request):
    quotes = QuoteRequest.objects.filter(
        buyer=request.user,
    ).select_related('product', 'seller')
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
    form = QuoteResponseForm(request.POST, instance=quote)
    if form.is_valid():
        form.save()
        messages.success(request, 'Your response was sent to the buyer.')
    else:
        messages.error(request, 'Please choose a valid response status.')
    return redirect('seller_quotes')


def _allowed_order_statuses(order, user):
    if order.seller_id == user.pk:
        transitions = {
            Order.Status.PENDING: [Order.Status.ACCEPTED, Order.Status.DECLINED],
            Order.Status.ACCEPTED: [Order.Status.PROCESSING, Order.Status.CANCELLED],
            Order.Status.PROCESSING: [Order.Status.SHIPPED, Order.Status.CANCELLED],
            Order.Status.SHIPPED: [Order.Status.DELIVERED],
        }
    elif order.buyer_id == user.pk:
        transitions = {
            Order.Status.PENDING: [Order.Status.CANCELLED],
            Order.Status.ACCEPTED: [Order.Status.CANCELLED],
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


@login_required
def create_order(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('owner'),
        slug=slug,
        is_active=True,
    )
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
    allowed_statuses = _allowed_order_statuses(order, request.user)
    status_form = (
        OrderStatusForm(allowed_statuses=allowed_statuses)
        if allowed_statuses else None
    )
    return render(request, 'gallery/order_detail.html', {
        'order': order,
        'order_messages': order.messages.select_related('sender'),
        'message_form': OrderMessageForm(),
        'status_form': status_form,
        'page_title': f'Order #{order.pk} - GAB',
    })


@login_required
@require_POST
def send_order_message(request, pk):
    order = _get_participant_order(request, pk)
    form = OrderMessageForm(request.POST)
    if form.is_valid():
        OrderMessage.objects.create(
            order=order,
            sender=request.user,
            body=form.cleaned_data['body'],
        )
    else:
        messages.error(request, 'Messages must contain up to 2,000 characters.')
    return redirect('order_detail', pk=order.pk)


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


def upload_image(request):
    if request.method == 'POST':
        form = GalleryImageForm(request.POST, request.FILES)
        if form.is_valid():
            img = form.save(commit=False)
            img.uploaded_by = request.user
            img.save()
            messages.success(
                request,
                f'Image "{img.title}" uploaded successfully and is now visible on the Images page!'
            )
            return redirect('images_show')
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
    my_imgs = GalleryImage.objects.filter(uploaded_by=request.user)
    context = {
        'images': my_imgs,
        'page_title': 'My Images - GAB Platform'
    }
    return render(request, 'gallery/my_images.html', context)


@login_required
@require_POST
def delete_image(request, pk):
    img = get_object_or_404(GalleryImage, pk=pk, uploaded_by=request.user)
    title = img.title
    img.delete()
    messages.success(request, f'Image "{title}" deleted successfully.')
    return redirect('my_images')