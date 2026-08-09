from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings

from .models import GeoapifyAPILog, GeocodedLocation, RouteEstimate
from .travel import get_route_estimate, normalize_location


@override_settings(GEOAPIFY_API_KEY="test-api-key")
class GeoapifyTravelTests(TestCase):
    def test_fetches_and_caches_a_driving_estimate(self):
        origin_response = Mock()
        origin_response.status_code = 200
        origin_response.raise_for_status.return_value = None
        origin_response.json.return_value = {
            "results": [
                {
                    "formatted": "Central Stadium, Phoenix, AZ",
                    "lat": 33.45,
                    "lon": -112.07,
                }
            ]
        }
        destination_response = Mock()
        destination_response.status_code = 200
        destination_response.raise_for_status.return_value = None
        destination_response.json.return_value = {
            "results": [
                {
                    "formatted": "North Field, Phoenix, AZ",
                    "lat": 33.60,
                    "lon": -112.10,
                }
            ]
        }
        route_response = Mock()
        route_response.status_code = 200
        route_response.raise_for_status.return_value = None
        route_response.json.return_value = {
            "features": [{"properties": {"time": 1234.4, "distance": 16093.2}}]
        }

        with patch(
            "pages.travel.requests.get",
            side_effect=[origin_response, destination_response, route_response],
        ) as request:
            estimate, fetched = get_route_estimate(
                "Central Stadium", "North Field"
            )
            cached, fetched_again = get_route_estimate(
                " central   stadium ", "north field"
            )

        self.assertTrue(fetched)
        self.assertFalse(fetched_again)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(estimate.pk, cached.pk)
        self.assertEqual(estimate.duration_seconds, 1234)
        self.assertEqual(estimate.distance_meters, 16093)
        self.assertEqual(GeocodedLocation.objects.count(), 2)
        self.assertEqual(RouteEstimate.objects.count(), 1)
        self.assertEqual(GeoapifyAPILog.objects.count(), 3)
        self.assertTrue(
            all(log.success for log in GeoapifyAPILog.objects.all())
        )
        self.assertTrue(
            all(
                log.request_params["apiKey"] == "[redacted]"
                for log in GeoapifyAPILog.objects.all()
            )
        )
        self.assertNotIn(
            "test-api-key",
            "".join(log.response_body for log in GeoapifyAPILog.objects.all()),
        )
        self.assertEqual(
            request.call_args_list[2].kwargs["params"]["apiKey"], "test-api-key"
        )

    def test_missing_key_does_not_make_requests(self):
        with override_settings(GEOAPIFY_API_KEY=""):
            with patch("pages.travel.requests.get") as request:
                estimate, fetched = get_route_estimate("One Field", "Two Field")

        self.assertIsNone(estimate)
        self.assertFalse(fetched)
        request.assert_not_called()

    def test_failed_request_is_logged(self):
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"message": "Invalid apiKey"}
        response.raise_for_status.side_effect = requests.HTTPError()

        with patch("pages.travel.requests.get", return_value=response):
            estimate, fetched = get_route_estimate("One Field", "Two Field")

        self.assertIsNone(estimate)
        self.assertTrue(fetched)
        log = GeoapifyAPILog.objects.get()
        self.assertFalse(log.success)
        self.assertEqual(log.response_status, 401)
        self.assertEqual(log.error_message, "Geoapify returned HTTP 401.")
        self.assertIn("Invalid apiKey", log.response_body)
        self.assertEqual(log.request_params["apiKey"], "[redacted]")

    def test_normalizes_location_cache_keys(self):
        self.assertEqual(
            normalize_location("  Central   Stadium  "), "central stadium"
        )
