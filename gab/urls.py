from pathlib import Path

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.db import connection
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET
from accounts.business_profiles import business_profiles_for
from gallery.models import SellerBanner
from gallery.views import serve_uploaded_media

def home_view(request):
    """Show attractive landing page for visitors, dashboard for logged-in users"""
    if request.user.is_authenticated:
        return redirect(request.user.get_dashboard_url())
    banners = list(SellerBanner.objects.filter(
        is_active=True,
        owner__is_active=True,
        seller_role='SHOPKEEPER',
    ).select_related('owner')[:4])
    profiles = business_profiles_for(
        (banner.owner_id, banner.seller_role)
        for banner in banners
        if banner.owner_id
    )
    for banner in banners:
        banner.seller_profile = profiles.get(
            (banner.owner_id, banner.seller_role),
        )
    return render(request, 'home.html', {
        'banners': banners,
    })


def health_check(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return HttpResponse('ok')


@require_GET
def service_worker(request):
    worker_path = Path(settings.BASE_DIR) / 'serviceworker.js'
    response = FileResponse(worker_path.open('rb'), content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


urlpatterns = [
    path('admin/', admin.site.urls),
    path('serviceworker.js', service_worker, name='service_worker'),
    path('accounts/', include('accounts.urls')),
    path('images/', include('gallery.urls')),   # Gallery app
    path('media/<path:media_path>', serve_uploaded_media, name='media_file'),
    path('health/', health_check, name='health_check'),
    path('', home_view, name='home'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])