"""Django settings. The app is stateless: no database, sessions or admin."""

import os

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
# Nothing here signs user data, but Django refuses to boot without a key.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "stateless-app-no-signed-data")

ALLOWED_HOSTS = [".vercel.app", "localhost", "127.0.0.1"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
USE_TZ = True
TIME_ZONE = "UTC"

INSTALLED_APPS = ["rest_framework", "planner"]

MIDDLEWARE = [
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

DATABASES = {}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "OPTIONS": {"MAX_ENTRIES": 20_000},
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
}

VALHALLA_URL = os.environ.get("VALHALLA_URL", "https://valhalla1.openstreetmap.de/route")
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org/route/v1/driving")
OPEN_METEO_URL = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
OPEN_METEO_API_KEY = os.environ.get("OPEN_METEO_API_KEY", "")
PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io/api/")
HTTP_HEADERS = {"User-Agent": "weather-aware-truck-routing/1.0"}
