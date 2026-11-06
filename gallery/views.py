import mimetypes
from pathlib import Path

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import SuspiciousFileOperation
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.utils._os import safe_join
from django.views.decorators.http import require_POST
from accounts.models import User
from .models import GalleryImage, Product, QuoteRequest, SellerBanner
from .forms import (
    GalleryImageForm,
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

    products = Product.objects.filter(is_active=True).select_related('owner')

    if category != 'all':
        products = products.filter(category=category)

    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(supplier__icontains=query)
            | Q(location__icontains=query)
        )

    if sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'rating':
        products = products.order_by('-rating', '-review_count')
    else:
        products = products.order_by('-is_featured', '-created_at')

    category_counts = {
        row['category']: row['total']
        for row in Product.objects.filter(is_active=True)
        .values('category')
        .annotate(total=Count('id'))
    }
    categories = [choice[0] for choice in Product.Category.choices]
    context = {
        'products': products,
        'banners': SellerBanner.objects.filter(
            is_active=True,
            owner__is_active=True,
        ).select_related('owner')[:4],
        'categories': categories,
        'category_options': [
            {'value': category, 'count': category_counts.get(category, 0)}
            for category in categories
        ],
        'selected_category': category,
        'query': query,
        'sort': sort,
        'total_products': products.count(),
        'page_title': 'Catalog - GAB Platform',
    }
    return render(request, 'gallery/catalog.html', context)


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('owner'),
        slug=slug,
        is_active=True,
    )
    related_products = Product.objects.filter(
        is_active=True,
        category=product.category,
    ).exclude(pk=product.pk)[:4]
    return render(request, 'gallery/product_detail.html', {
        'product': product,
        'related_products': related_products,
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


@login_required
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