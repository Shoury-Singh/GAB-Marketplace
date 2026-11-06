"""
WSGI config for the GAB platform project.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gab.production')

application = get_wsgi_application()
