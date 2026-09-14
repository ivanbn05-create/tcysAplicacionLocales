#!/bin/sh
set -eu

: "${SUCURSAL_CLAVE:?SUCURSAL_CLAVE es obligatoria}"
: "${SUCURSAL_NOMBRE:?SUCURSAL_NOMBRE es obligatoria}"

python manage.py verificar_identidad_local
python manage.py migrate --noinput
set -- aprovisionar_sucursal \
    --clave "$SUCURSAL_CLAVE" \
    --nombre "$SUCURSAL_NOMBRE"
if [ -n "${SUCURSAL_ID:-}" ]; then
    set -- "$@" --sucursal-id "$SUCURSAL_ID"
fi
python manage.py "$@"

python manage.py collectstatic --noinput
exec gunicorn pos.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60
