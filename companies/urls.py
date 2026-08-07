"""Routes for company management."""

from django.urls import path

from companies import views

app_name = "companies"

urlpatterns = [
    path("", views.company_list, name="list"),
    path("new/", views.company_create, name="create"),
    path("<int:pk>/", views.company_detail, name="detail"),
    path("<int:pk>/edit/", views.company_edit, name="edit"),
    path("<int:pk>/toggle-active/", views.company_toggle_active, name="toggle_active"),
    path("<int:pk>/update-jobs/", views.company_update_jobs, name="update_jobs"),
]
