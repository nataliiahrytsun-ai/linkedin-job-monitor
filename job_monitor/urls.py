"""URL configuration for the job monitor project."""

from django.contrib import admin
from django.urls import include, path

from job_monitor.views import home, update_all

urlpatterns = [
    path("", home, name="home"),
    path("update-all/", update_all, name="update_all"),
    path("companies/", include("companies.urls")),
    path("jobs/", include("jobs.urls")),
    path("admin/", admin.site.urls),
]
