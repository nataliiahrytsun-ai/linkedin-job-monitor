from django.urls import path

from discovery import views

app_name = "discovery"
urlpatterns = [
    path("companies/<int:company_pk>/start/", views.start, name="start"),
    path(
        "companies/<int:company_pk>/candidates/<int:candidate_pk>/confirm/",
        views.confirm,
        name="confirm",
    ),
    path(
        "companies/<int:company_pk>/candidates/<int:candidate_pk>/connect/",
        views.connect,
        name="connect",
    ),
    path(
        "companies/<int:company_pk>/candidates/connect-selected/",
        views.connect_selected,
        name="connect_selected",
    ),
    path(
        "companies/<int:company_pk>/candidates/<int:candidate_pk>/revalidate/",
        views.revalidate,
        name="revalidate",
    ),
    path(
        "companies/<int:company_pk>/candidates/<int:candidate_pk>/ignore/",
        views.ignore,
        name="ignore",
    ),
    path(
        "companies/<int:company_pk>/candidates/<int:candidate_pk>/restore/",
        views.restore,
        name="restore",
    ),
]
