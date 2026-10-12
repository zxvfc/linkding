from django.test import TestCase
from django.urls import reverse

from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class BookmarkListPageCountTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def get_heading(self, response):
        soup = self.make_soup(response.content.decode())
        heading = soup.select_one("h1#main-heading")
        self.assertIsNotNone(heading)
        return heading

    def assertHeadingCount(self, response, list_title: str, expected_count: int):
        heading = self.get_heading(response)
        # heading still contains the page title
        self.assertIn(list_title, heading.get_text())

        count = heading.select_one("span")
        self.assertIsNotNone(count)

        # visible text is the total count
        self.assertEqual(count.get_text(strip=True), str(expected_count))

        # screen reader label reflects the total number of bookmarks (R3)
        self.assertEqual(count.get("aria-label"), f"{expected_count} bookmarks")

        # muted styling next to the title (R1)
        self.assertIn("text-muted", count.get("class", []))

    def assertNoHeadingCount(self, response, list_title: str):
        heading = self.get_heading(response)

        # heading contains only the title, no count element (R2)
        self.assertIn(list_title, heading.get_text())
        self.assertIsNone(heading.select_one("span"))
        self.assertIsNone(heading.select_one("[aria-label]"))

    # R1: total selection count shown in muted styling, reflecting the whole
    # filtered result set rather than the current page.

    def test_r1_index_heading_shows_total_across_pagination_pages(self):
        self.setup_numbered_bookmarks(35, prefix="foo")

        page_1 = self.client.get(reverse("linkding:bookmarks.index") + "?q=foo")
        self.assertHeadingCount(page_1, "Bookmarks", 35)

        # page 2 shows only the remaining 5 items, but the total stays 35
        page_2 = self.client.get(
            reverse("linkding:bookmarks.index") + "?q=foo&page=2"
        )
        heading = self.get_heading(page_2)
        self.assertEqual(
            len(self.make_soup(page_2.content.decode()).select("ul.bookmark-list > li")),
            5,
        )
        self.assertIsNotNone(heading.select_one("span"))
        self.assertEqual(heading.select_one("span").get_text(strip=True), "35")

    def test_r1_archived_heading_shows_total_across_pagination_pages(self):
        self.setup_numbered_bookmarks(35, prefix="foo", archived=True)

        page_1 = self.client.get(reverse("linkding:bookmarks.archived") + "?q=foo")
        self.assertHeadingCount(page_1, "Archived bookmarks", 35)

        page_2 = self.client.get(
            reverse("linkding:bookmarks.archived") + "?q=foo&page=2"
        )
        heading = self.get_heading(page_2)
        self.assertEqual(
            len(self.make_soup(page_2.content.decode()).select("ul.bookmark-list > li")),
            5,
        )
        self.assertIsNotNone(heading.select_one("span"))
        self.assertEqual(heading.select_one("span").get_text(strip=True), "35")

    def test_r1_shared_heading_shows_total_across_pagination_pages(self):
        other_user = self.setup_user(enable_sharing=True)
        self.setup_numbered_bookmarks(35, prefix="foo", shared=True, user=other_user)

        page_1 = self.client.get(reverse("linkding:bookmarks.shared") + "?q=foo")
        self.assertHeadingCount(page_1, "Shared bookmarks", 35)

        page_2 = self.client.get(
            reverse("linkding:bookmarks.shared") + "?q=foo&page=2"
        )
        heading = self.get_heading(page_2)
        self.assertEqual(
            len(self.make_soup(page_2.content.decode()).select("ul.bookmark-list > li")),
            5,
        )
        self.assertIsNotNone(heading.select_one("span"))
        self.assertEqual(heading.select_one("span").get_text(strip=True), "35")

    def test_r1_heading_count_reflects_filtered_selection(self):
        self.setup_numbered_bookmarks(3, prefix="foo")
        self.setup_numbered_bookmarks(4, prefix="bar")

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=foo")

        self.assertHeadingCount(response, "Bookmarks", 3)

    # R2: no count rendered when the current selection is empty.

    def test_r2_index_heading_has_no_count_when_empty(self):
        response = self.client.get(reverse("linkding:bookmarks.index"))
        self.assertNoHeadingCount(response, "Bookmarks")

    def test_r2_index_heading_has_no_count_when_search_has_no_results(self):
        self.setup_bookmark(title="Test Bookmark")

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=missing")

        self.assertNoHeadingCount(response, "Bookmarks")

    def test_r2_archived_heading_has_no_count_when_empty(self):
        response = self.client.get(reverse("linkding:bookmarks.archived"))
        self.assertNoHeadingCount(response, "Archived bookmarks")

    def test_r2_shared_heading_has_no_count_when_empty(self):
        response = self.client.get(reverse("linkding:bookmarks.shared"))
        self.assertNoHeadingCount(response, "Shared bookmarks")

    # R3: count exposed to screen readers as "N bookmarks".

    def test_r3_index_count_has_accessible_label(self):
        self.setup_numbered_bookmarks(42, prefix="foo")

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=foo")

        self.assertHeadingCount(response, "Bookmarks", 42)
        heading = self.get_heading(response)
        self.assertEqual(heading.select_one("span").get("aria-label"), "42 bookmarks")

    def test_r3_archived_count_has_accessible_label(self):
        self.setup_numbered_bookmarks(42, prefix="foo", archived=True)

        response = self.client.get(reverse("linkding:bookmarks.archived") + "?q=foo")

        self.assertHeadingCount(response, "Archived bookmarks", 42)

    def test_r3_shared_count_has_accessible_label(self):
        other_user = self.setup_user(enable_sharing=True)
        self.setup_numbered_bookmarks(42, prefix="foo", shared=True, user=other_user)

        response = self.client.get(reverse("linkding:bookmarks.shared") + "?q=foo")

        self.assertHeadingCount(response, "Shared bookmarks", 42)
