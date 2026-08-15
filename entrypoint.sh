#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py cargar_datos_iniciales
python manage.py collectstatic --noinput
exec gunicorn pos.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60
