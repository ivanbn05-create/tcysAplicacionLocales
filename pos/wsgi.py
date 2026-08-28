import os

from django.core.wsgi import get_wsgi_application

# WSGI es exclusivamente el punto de entrada de producción. No se hereda una
# selección ambiental que pudiera dejar DEBUG o la autenticación desactivados.
os.environ["DJANGO_SETTINGS_MODULE"] = "pos.settings"
application = get_wsgi_application()
