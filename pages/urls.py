from django.urls import path

from .views import (
    AboutPageView,
    HomePageView,
    add_calendar,
    add_saved_link,
    calendar_edit,
    calendar_preview,
    calendar_rules,
    calendar_visibility_rules,
    clear_geoapify_api_logs,
    confirm_calendar,
    delete_calendar,
    delete_calendar_rule,
    delete_calendar_visibility_rule,
    delete_saved_link,
    edit_calendar_rule,
    edit_calendar_visibility_rule,
    edit_saved_link,
    geoapify_api_logs,
    populate_demo_calendar,
    refresh_all_calendars,
    refresh_calendar,
    toggle_calendar,
    toggle_calendar_rule,
    toggle_calendar_visibility_rule,
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
    path("links/add/", add_saved_link, name="saved_link_add"),
    path("links/<int:pk>/edit/", edit_saved_link, name="saved_link_edit"),
    path("links/<int:pk>/delete/", delete_saved_link, name="saved_link_delete"),
    path(
        "calendars/refresh-all/",
        refresh_all_calendars,
        name="calendars_refresh_all",
    ),
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
        "calendars/<int:pk>/visibility/",
        calendar_visibility_rules,
        name="calendar_visibility_rules",
    ),
    path(
        "calendars/<int:pk>/visibility/<int:rule_pk>/edit/",
        edit_calendar_visibility_rule,
        name="calendar_visibility_rule_edit",
    ),
    path(
        "calendars/<int:pk>/visibility/<int:rule_pk>/toggle/",
        toggle_calendar_visibility_rule,
        name="calendar_visibility_rule_toggle",
    ),
    path(
        "calendars/<int:pk>/visibility/<int:rule_pk>/delete/",
        delete_calendar_visibility_rule,
        name="calendar_visibility_rule_delete",
    ),
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
