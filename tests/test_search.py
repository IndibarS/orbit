"""Search relevance and untrusted metadata rendering regressions."""

import re
import unittest
from html import unescape

from orbit_gtk.backend.search import SmartSearch


class SmartSearchTests(unittest.TestCase):
    def test_exact_prefix_and_literal_before_spelling(self):
        matcher = SmartSearch("firefox")
        names = ["firefx", "libfirefox", "firefox-esr", "firefox"]
        self.assertEqual(
            sorted(names, key=lambda n: matcher.name_match(n)[0]),
            ["firefox", "firefox-esr", "libfirefox", "firefx"],
        )

    def test_typing_errors_in_name_components(self):
        for query, name in [
            ("firfox", "firefox-esr"),
            ("firefoc", "firefox"),
            ("fierfox", "firefox"),
            ("pyhton", "python3-python"),
            ("chromuim", "chromium"),
        ]:
            with self.subTest(query=query):
                self.assertIsNotNone(SmartSearch(query).name_match(name))

    def test_short_and_unrelated_terms_do_not_fuzzy_match(self):
        for query, name in [
            ("git", "got"),
            ("sl", "sql"),
            ("firefox", "firmware"),
            (".*", "anything"),
        ]:
            self.assertIsNone(SmartSearch(query).name_match(name))

    def test_screenshot_queries(self):
        for query, name in [
            ("hollywd", "hollywood"),
            ("h*d", "hollywood"),
            ("?o*", "hollywood"),
            ("[a-z]h*", "thunar"),
        ]:
            with self.subTest(query=query):
                self.assertIsNotNone(SmartSearch(query).name_match(name))
        self.assertEqual(
            SmartSearch("hollywd").markup("hollywood"),
            "<span background='#f6d32d' foreground='#241f00'>hollyw</span>oo<span background='#f6d32d' foreground='#241f00'>d</span>",
        )
        self.assertEqual(
            SmartSearch("h*d").markup("hollywood"),
            "<span background='#f6d32d' foreground='#241f00'>h</span>ollywoo<span background='#f6d32d' foreground='#241f00'>d</span>",
        )
        self.assertEqual(
            SmartSearch("?o*").markup("hollywood"),
            "h<span background='#f6d32d' foreground='#241f00'>o</span>llywood",
        )
        self.assertIsNone(SmartSearch("[a-z]h*").name_match("hollywood"))

    def test_pattern_alignment_and_literal_punctuation(self):
        for pattern, name in [
            ("lib*dev", "libfoo-dev"),
            ("[!a]*", "bash"),
            ("[]a]*", "]abc"),
            ("a**b?c", "axybzc"),
            ("g++", "g++"),
            ("libc6.1", "libc6.1"),
        ]:
            self.assertIsNotNone(SmartSearch(pattern).name_match(name))
        self.assertEqual(
            SmartSearch("a*b*c").markup("abbc"),
            "<span background='#f6d32d' foreground='#241f00'>ab</span>b<span background='#f6d32d' foreground='#241f00'>c</span>",
        )
        self.assertIsNone(SmartSearch("h*d").name_match("hollywood-doc"))
        self.assertFalse(SmartSearch("h*d").matches_text("hollywood"))
        self.assertIsNone(SmartSearch("[a-").name_match("apt"))
        self.assertIsNone(SmartSearch("a*" * 100 + "z").name_match("a" * 200))

    def test_abbreviations_and_multiple_typing_errors(self):
        for query, name in [
            ("hlwd", "hollywood"),
            ("hoklywops", "hollywood"),
            ("frfx", "firefox"),
            ("chrmm", "chromium"),
            ("hlwd", "hollywood-doc"),
        ]:
            with self.subTest(query=query):
                self.assertIsNotNone(SmartSearch(query).name_match(name))
        self.assertEqual(
            SmartSearch("hlwd").markup("hollywood"),
            "<span background='#f6d32d' foreground='#241f00'>h</span>o<span background='#f6d32d' foreground='#241f00'>l</span>ly<span background='#f6d32d' foreground='#241f00'>w</span>oo<span background='#f6d32d' foreground='#241f00'>d</span>",
        )
        self.assertLess(
            SmartSearch("hlwd").name_match("hlwd")[0],
            SmartSearch("hlwd").name_match("hollywood")[0],
        )
        self.assertIsNone(SmartSearch("hlwd").name_match("hello-world-documentation"))
        self.assertIsNone(SmartSearch("hlwd").name_match("worldhello"))
        matcher = SmartSearch("hlwd")
        self.assertLess(matcher.name_match("hollywood")[0], matcher.name_match("php-htmlawed")[0])
        self.assertIsNone(SmartSearch("*/d").name_match("hollywood"))

    def test_multiple_words_and_metadata(self):
        matcher = SmartSearch("python requets")
        self.assertIsNotNone(matcher.name_match("python3-requests"))
        self.assertIsNone(matcher.name_match("python3"))
        self.assertTrue(SmartSearch("image editor").matches_text("Editor for digital images"))
        self.assertFalse(SmartSearch("image editor").matches_text("Text editor"))

    def test_query_bounds(self):
        self.assertIsNone(SmartSearch(" ").name_match("apt"))
        with self.assertRaises(ValueError):
            SmartSearch("a" * 257)

    def test_highlights_and_escapes(self):
        self.assertEqual(
            SmartSearch("fire").markup("firefox"),
            "<span background='#f6d32d' foreground='#241f00'>fire</span>fox",
        )
        self.assertEqual(
            SmartSearch("firfox").markup("firefox"),
            "<span background='#f6d32d' foreground='#241f00'>fir</span>e<span background='#f6d32d' foreground='#241f00'>fox</span>",
        )
        name = '<b>firefox & "friends"</b>'
        markup = SmartSearch("firefox").markup(name)
        self.assertIn("&lt;b&gt;", markup)
        self.assertEqual(unescape(re.sub(r"<span[^>]*>|</span>", "", markup)), name)
        self.assertEqual(
            SmartSearch("ss").markup("Straße"),
            "Stra<span background='#f6d32d' foreground='#241f00'>ß</span>e",
        )

    def test_long_input_is_not_a_pattern(self):
        self.assertIsNone(SmartSearch("(a+)+$").name_match("a" * 100000))
