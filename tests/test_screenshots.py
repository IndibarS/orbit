"""Screenshot selection and bounded transfer tests, without network access."""

import io
import unittest
from unittest.mock import MagicMock, patch

from orbit_gtk.backend.appstream import PackageMetadata
from orbit_gtk.backend.models import PackageInfo
from orbit_gtk.backend.screenshots import _ImageRedirect, fetch_screenshot, valid_image_url


def image(url, width):
    result = MagicMock()
    result.get_url.return_value = url
    result.get_width.return_value = width
    result.get_height.return_value = width // 2
    return result


class ScreenshotTests(unittest.TestCase):
    def test_selects_thumbnail_preserves_caption_without_icon(self):
        component = MagicMock()
        component.get_icons.return_value = []
        component.get_pkgnames.return_value = ["example"]
        shot = MagicMock()
        shot.get_caption.return_value = "Example & details"
        shot.get_images.return_value = [
            image("https://example.org/full", 1920),
            image("https://example.org/thumb", 752),
        ]
        component.get_screenshots_all.return_value = [shot]
        with patch.object(PackageMetadata, "_load_components", return_value=[component]):
            result = PackageMetadata().decorate([PackageInfo(name="example:amd64")])[0]
        self.assertEqual(result.screenshots[0].url, "https://example.org/thumb")
        self.assertEqual(result.screenshots[0].caption, "Example & details")
        self.assertFalse(result.icon_file)

    def test_missing_or_unsupported_images_omitted(self):
        component, shot = MagicMock(), MagicMock()
        shot.get_images.return_value = [
            image("file:///tmp/image", 752),
            image("https://example.org/huge", 20000),
        ]
        component.get_screenshots_all.return_value = [shot]
        self.assertEqual(PackageMetadata._component_screenshots(component), ())

    def test_rejects_credentials_non_https_and_redirects(self):
        for url in (
            "file:///etc/passwd",
            "http://example.org/a",
            "https://user:pass@example.org/a",
            "https://example.org/a b",
            "https://[bad",
        ):
            self.assertFalse(valid_image_url(url))
            with self.assertRaises(ValueError):
                fetch_screenshot(url)
        with self.assertRaises(ValueError):
            _ImageRedirect().redirect_request(None, None, 302, "", {}, "file:///etc/passwd")

    def test_transfer_cap_even_without_content_length(self):
        for headers in ({}, {"Content-Length": "9"}):
            response = MagicMock()
            response.__enter__.return_value = response
            response.headers = headers
            response.read1.side_effect = io.BytesIO(b"123456789").read
            with (
                patch("orbit_gtk.backend.screenshots.build_opener") as opener,
                patch("orbit_gtk.backend.screenshots.MAX_IMAGE_BYTES", 8),
            ):
                opener.return_value.open.return_value = response
                with self.assertRaisesRegex(ValueError, "too large"):
                    fetch_screenshot("https://example.org/image")

    def test_download_bytes(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read1.side_effect = io.BytesIO(b"image data").read
        with patch("orbit_gtk.backend.screenshots.build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(fetch_screenshot("https://example.org/image"), b"image data")
