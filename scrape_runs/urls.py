"""Routes for scrape-run history and read-only status polling."""

from django.urls import path

from scrape_runs import views

app_name = "scrape_runs"

urlpatterns = [
    path("", views.scrape_run_list, name="list"),
    path("status/", views.scrape_run_status, name="status"),
]
