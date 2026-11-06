"""
ASGI config for GAB project.
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gab.settings')

application = get_asgi_application()