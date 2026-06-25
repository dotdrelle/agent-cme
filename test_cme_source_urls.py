import unittest

from cme_source_urls import extract_confluence_url, parse_confluence_source_url


class ConfluenceSourceUrlTests(unittest.TestCase):
    def test_extracts_markdown_link(self):
        url = "http://confluence.meteo.fr/spaces/ODG/pages/244062217/ACPI"
        self.assertEqual(extract_confluence_url(f"- [{url}]({url})"), url)
        self.assertEqual(extract_confluence_url(f"ACPI ([ACPI]({url}))"), url)

    def test_recognizes_modern_page_url(self):
        urls = [
            "http://confluence.meteo.fr/spaces/JDLCDPPO/pages/685283652/Synth%C3%A9se+des+demo",
            "http://confluence.meteo.fr/spaces/DIRNCTTI/pages/669285729/JUNO+-+Inventaire+mat%C3%A9riel",
            "http://confluence.meteo.fr/spaces/MTF/pages/161513514/7.Liste+des+serveurs",
            "http://confluence.meteo.fr/spaces/MSI/pages/609477636/PP+et+SC+WebIAA",
            "http://confluence.meteo.fr/spaces/ODG/pages/244062217/ACPI",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(
                    parse_confluence_source_url(url),
                    {"type": "page", "url": url},
                )

    def test_recognizes_space_url(self):
        self.assertEqual(
            parse_confluence_source_url(
                "https://company.atlassian.net/wiki/spaces/JDLCDPPO"
            ),
            {
                "type": "space",
                "base_url": "https://company.atlassian.net/wiki",
                "space": "JDLCDPPO",
            },
        )

    def test_preserves_viewpage_url_with_personal_space(self):
        url = (
            "http://confluence.meteo.fr/pages/viewpage.action"
            "?pageId=677699540&spaceKey=~vincent.loibl%40meteo.fr"
            "&title=concept%2Basync%2Brequete"
        )
        self.assertEqual(parse_confluence_source_url(url), {"type": "page", "url": url})

    def test_recognizes_display_space_and_page(self):
        self.assertEqual(
            parse_confluence_source_url("http://confluence.meteo.fr/display/ODG"),
            {
                "type": "space",
                "base_url": "http://confluence.meteo.fr",
                "space": "ODG",
            },
        )
        page_url = "http://confluence.meteo.fr/display/ODG/ACPI"
        self.assertEqual(
            parse_confluence_source_url(page_url),
            {"type": "page", "url": page_url},
        )


if __name__ == "__main__":
    unittest.main()
