"""URL configuration for the job monitor project."""

from django.contrib import admin
from django.urls import include, path

from job_monitor.views import home

urlpatterns = [
    path("", home, name="home"),
    path("companies/", include("companies.urls")),
    path("admin/", admin.site.urls),
]
