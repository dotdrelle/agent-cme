import unittest

from cme_source_urls import extract_confluence_url, parse_confluence_source_url


class ConfluenceSourceUrlTests(unittest.TestCase):
    def test_extracts_markdown_link(self):
        url = "http://confluence.example.com/spaces/DOC/pages/100000001/Overview"
        self.assertEqual(extract_confluence_url(f"- [{url}]({url})"), url)
        self.assertEqual(extract_confluence_url(f"Overview ([Overview]({url}))"), url)

    def test_recognizes_modern_page_url(self):
        urls = [
            "http://confluence.example.com/spaces/PROJ/pages/100000002/Release+Notes",
            "http://confluence.example.com/spaces/TEAM/pages/100000003/Architecture",
            "http://confluence.example.com/spaces/OPS/pages/100000004/Server+List",
            "http://confluence.example.com/spaces/DEV/pages/100000005/API+Reference",
            "http://confluence.example.com/spaces/DOC/pages/100000001/Overview",
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
                "https://company.atlassian.net/wiki/spaces/PROJ"
            ),
            {
                "type": "space",
                "base_url": "https://company.atlassian.net/wiki",
                "space": "PROJ",
            },
        )

    def test_preserves_viewpage_url_with_personal_space(self):
        url = (
            "http://confluence.example.com/pages/viewpage.action"
            "?pageId=100000006&spaceKey=~user.name%40example.com"
            "&title=concept%2Basync%2Brequete"
        )
        self.assertEqual(parse_confluence_source_url(url), {"type": "page", "url": url})

    def test_recognizes_display_space_and_page(self):
        self.assertEqual(
            parse_confluence_source_url("http://confluence.example.com/display/DOC"),
            {
                "type": "space",
                "base_url": "http://confluence.example.com",
                "space": "DOC",
            },
        )
        page_url = "http://confluence.example.com/display/DOC/Overview"
        self.assertEqual(
            parse_confluence_source_url(page_url),
            {"type": "page", "url": page_url},
        )


if __name__ == "__main__":
    unittest.main()
