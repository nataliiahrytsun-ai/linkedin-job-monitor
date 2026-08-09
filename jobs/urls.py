"""Routes for the global jobs overview."""

from django.urls import path  # type: ignore[import-untyped]

from jobs import views

app_name = "jobs"

urlpatterns = [
    path("", views.job_list, name="list"),
    path("<int:pk>/", views.job_detail, name="detail"),
]
