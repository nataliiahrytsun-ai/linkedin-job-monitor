"""URL configuration for the job monitor project."""

from django.contrib import admin
from django.urls import path

from job_monitor.views import home

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
]
