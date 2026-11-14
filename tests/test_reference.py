import unittest

from hentai_telegram.providers.reference import parse_reference


class TestReference(unittest.TestCase):
    def test_parse_id(self):
        self.assertEqual(parse_reference("12345"), "12345")
        self.assertEqual(parse_reference("JM12345"), "12345")
        self.assertEqual(parse_reference("jm12345"), "12345")

    def test_parse_allowlisted_url(self):
        self.assertEqual(parse_reference("https://example.test/album/42/", ("example.test",)), "42")
        self.assertEqual(parse_reference("https://example.test/photo/42", ("example.test",)), "42")

    def test_parse_rejects_untrusted(self):
        for value in ["0", "abc", "http://example.test/album/42", "https://evil.test/album/42"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_reference(value, ("example.test",))


if __name__ == "__main__":
    unittest.main()
