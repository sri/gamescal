from django.urls import path

from .views import (
    AboutPageView,
    HomePageView,
    add_calendar,
    calendar_edit,
    calendar_preview,
    calendar_rules,
    clear_geoapify_api_logs,
    confirm_calendar,
    delete_calendar,
    delete_calendar_rule,
    edit_calendar_rule,
    geoapify_api_logs,
    populate_demo_calendar,
    refresh_calendar,
    toggle_calendar,
    toggle_calendar_rule,
)

urlpatterns = [
    path("", HomePageView.as_view(), name="home"),
    path("about/", AboutPageView.as_view(), name="about"),
    path("developer/geoapify-logs/", geoapify_api_logs, name="geoapify_api_logs"),
    path(
        "developer/geoapify-logs/clear/",
        clear_geoapify_api_logs,
        name="geoapify_api_logs_clear",
    ),
    path("calendars/add/", add_calendar, name="calendar_add"),
    path(
        "calendars/demo/populate/",
        populate_demo_calendar,
        name="calendar_demo_populate",
    ),
    path(
        "calendars/preview/<str:token>/",
        calendar_preview,
        name="calendar_preview",
    ),
    path(
        "calendars/preview/<str:token>/confirm/",
        confirm_calendar,
        name="calendar_confirm",
    ),
    path("calendars/<int:pk>/edit/", calendar_edit, name="calendar_edit"),
    path(
        "calendars/<int:pk>/refresh/", refresh_calendar, name="calendar_refresh"
    ),
    path("calendars/<int:pk>/toggle/", toggle_calendar, name="calendar_toggle"),
    path("calendars/<int:pk>/delete/", delete_calendar, name="calendar_delete"),
    path("calendars/<int:pk>/rules/", calendar_rules, name="calendar_rules"),
    path(
        "calendars/<int:pk>/rules/<int:rule_pk>/edit/",
        edit_calendar_rule,
        name="calendar_rule_edit",
    ),
    path(
        "calendars/<int:pk>/rules/<int:rule_pk>/toggle/",
        toggle_calendar_rule,
        name="calendar_rule_toggle",
    ),
    path(
        "calendars/<int:pk>/rules/<int:rule_pk>/delete/",
        delete_calendar_rule,
        name="calendar_rule_delete",
    ),
]
