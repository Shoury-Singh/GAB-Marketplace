"""Production settings for deployments such as Render."""

import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *


if os.environ.get('DJANGO_DEBUG', '').lower() in {'true', '1', 'yes'}:
    raise ImproperlyConfigured('DJANGO_DEBUG must not be enabled with production settings.')

if not os.environ.get('DJANGO_SECRET_KEY'):
    raise ImproperlyConfigured('DJANGO_SECRET_KEY must be configured for production.')

if not os.environ.get('DATABASE_URL'):
    raise ImproperlyConfigured('DATABASE_URL must be configured for production.')

DEBUG = False
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
