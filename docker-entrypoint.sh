#!/bin/sh
set -e
python manage.py bootstrap
exec "$@"
