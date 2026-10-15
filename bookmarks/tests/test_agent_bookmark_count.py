from django.test import TestCase
from django.urls import reverse

from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class BookmarkHeadingCountTestCase(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    """Tests for the bookmark count counter rendered next to the page heading
    (R1-R5) and the hooks keeping it in sync after partial updates (R6)."""

    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def make_page_soup(self, response):
        return self.make_soup(response.content.decode())

    def get_heading(self, soup):
        heading = soup.select_one("h1#main-heading")
        self.assertIsNotNone(heading, "Expected <h1 id='main-heading'> to be rendered")
        return heading

    def get_heading_text(self, soup):
        return " ".join(self.get_heading(soup).get_text().split())

    def get_counter(self, soup):
        return soup.select_one("[data-bookmark-count]")

    # R1: heading displays the total count of the current selection as a muted number

    def test_r1_index_heading_shows_total_count_of_selection(self):
        profile = self.get_or_create_test_user().profile
        profile.items_per_page = 10
        profile.save()
        self.setup_numbered_bookmarks(42)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_page_soup(response)

        self.assertEqual(self.get_heading_text(soup), "Bookmarks 42")

        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "42")
        # counter is rendered inside the heading, next to the title
        heading = self.get_heading(soup)
        self.assertIsNotNone(heading.select_one("[data-bookmark-count]"))
        # count is the full selection size, not the current pagination page size
        page_items = soup.select("ul.bookmark-list > li")
        self.assertEqual(len(page_items), 10)

    def test_r1_archived_heading_shows_total_count_of_selection(self):
        self.setup_numbered_bookmarks(2)
        self.setup_numbered_bookmarks(5, archived=True)

        response = self.client.get(reverse("linkding:bookmarks.archived"))
        soup = self.make_page_soup(response)

        self.assertEqual(self.get_heading_text(soup), "Archived bookmarks 5")

        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "5")

    def test_r1_shared_heading_shows_total_count_of_selection(self):
        sharing_user = self.setup_user(enable_sharing=True)
        other_user = self.setup_user(enable_sharing=False)
        for i in range(4):
            self.setup_bookmark(shared=True, user=sharing_user, title=f"Shared {i}")
        # shared bookmark of a user without sharing enabled is not part of the selection
        self.setup_bookmark(shared=True, user=other_user, title="Not visible")

        response = self.client.get(reverse("linkding:bookmarks.shared"))
        soup = self.make_page_soup(response)

        self.assertEqual(self.get_heading_text(soup), "Shared bookmarks 4")

        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "4")

    # R2: counter reflects the active search query and filters

    def test_r2_counter_reflects_search_query(self):
        self.setup_numbered_bookmarks(2, prefix="python")
        self.setup_numbered_bookmarks(3, prefix="django")

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=python")
        soup = self.make_page_soup(response)

        self.assertEqual(self.get_heading_text(soup), "Bookmarks 2")

        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "2")

    def test_r2_counter_reflects_active_filters(self):
        self.setup_numbered_bookmarks(3, prefix="unread", unread=True)
        self.setup_numbered_bookmarks(2, prefix="read", unread=False)

        response = self.client.get(reverse("linkding:bookmarks.index") + "?unread=yes")
        soup = self.make_page_soup(response)

        self.assertEqual(self.get_heading_text(soup), "Bookmarks 3")

        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "3")

    # R3: no counter is rendered when the current selection is empty

    def test_r3_no_counter_rendered_when_selection_is_empty(self):
        self.setup_numbered_bookmarks(3, prefix="python")

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=nomatch")
        soup = self.make_page_soup(response)

        self.assertIsNone(soup.select_one("[data-bookmark-count]"))
        self.assertIsNone(soup.select_one("ul.bookmark-list"))
        # the existing empty state is shown instead
        empty_state = soup.select_one("div.empty")
        self.assertIsNotNone(empty_state)
        self.assertIn("You have no bookmarks yet", empty_state.get_text())
        # heading still renders the title
        self.assertEqual(self.get_heading_text(soup), "Bookmarks")

    # R4: counter is accessible to screen readers

    def test_r4_counter_carries_screen_reader_label(self):
        self.setup_numbered_bookmarks(42)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_page_soup(response)

        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        # visible text content is only the number
        self.assertEqual(counter.get_text(), "42")
        # screen readers announce the meaning of the number
        self.assertEqual(counter.attrs.get("aria-label"), "42 bookmarks")

    # R5: counter uses the existing .text-muted utility class

    def test_r5_counter_uses_text_muted_utility_class(self):
        self.setup_numbered_bookmarks(2)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_page_soup(response)

        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        self.assertIn("text-muted", counter.get("class", []))
        # appearance comes from the existing utility class, not inline styles
        self.assertNotIn("style", counter.attrs)

    # R6: hooks for the client-side sync after in-place (Turbo Stream) updates

    def test_r6_page_renders_sync_hooks_for_counter_and_list_total(self):
        self.setup_numbered_bookmarks(3)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_page_soup(response)

        # heading counter the JS component updates
        counter = self.get_counter(soup)
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "3")
        self.assertEqual(counter.attrs.get("aria-label"), "3 bookmarks")
        self.assertNotIn("d-none", counter.get("class", []))
        # list total the JS component re-reads after updates
        bookmark_list = soup.select_one("ul.bookmark-list")
        self.assertIsNotNone(bookmark_list)
        self.assertEqual(bookmark_list.attrs.get("data-bookmarks-total"), "3")
        # update event dispatched after every list render
        self.assertIn("bookmark-list-updated", response.content.decode())

    def test_r6_archive_partial_update_returns_new_total(self):
        bookmarks = self.setup_numbered_bookmarks(3)

        # page initially shows the counter
        response = self.client.get(reverse("linkding:bookmarks.index"))
        counter = self.get_counter(self.make_page_soup(response))
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "3")

        response = self.client.post(
            reverse("linkding:bookmarks.index.action"),
            {"archive": [bookmarks[0].id]},
            HTTP_ACCEPT="text/vnd.turbo-stream.html",
        )

        bookmarks[0].refresh_from_db()
        self.assertTrue(bookmarks[0].is_archived)

        html = response.content.decode()
        soup = self.make_soup(html)

        # updated list carries the new total for the JS component
        updated_list = soup.select_one('ul.bookmark-list[data-bookmarks-total="2"]')
        self.assertIsNotNone(updated_list)
        self.assertEqual(len(soup.select("ul.bookmark-list > li")), 2)
        # update event is dispatched so the component can refresh the heading
        self.assertIn("bookmark-list-updated", html)
        # the heading (with its counter) is not replaced by the stream,
        # so it persists in the DOM and is updated in place
        self.assertIsNone(soup.select_one("[data-bookmark-count]"))

    def test_r6_partial_update_to_empty_renders_empty_state_without_total(self):
        bookmarks = self.setup_numbered_bookmarks(2)

        # page initially shows the counter, visible (not hidden)
        response = self.client.get(reverse("linkding:bookmarks.index"))
        counter = self.get_counter(self.make_page_soup(response))
        self.assertIsNotNone(counter)
        self.assertEqual(counter.get_text(strip=True), "2")
        self.assertNotIn("d-none", counter.get("class", []))

        # bulk archive all remaining bookmarks
        response = self.client.post(
            reverse("linkding:bookmarks.index.action"),
            {
                "bulk_action": ["bulk_archive"],
                "bulk_execute": [""],
                "bookmark_id": [str(bookmark.id) for bookmark in bookmarks],
            },
            HTTP_ACCEPT="text/vnd.turbo-stream.html",
        )

        for bookmark in bookmarks:
            bookmark.refresh_from_db()
            self.assertTrue(bookmark.is_archived)

        html = response.content.decode()
        soup = self.make_soup(html)

        # no total hook is rendered for an empty selection,
        # so the JS component hides the heading counter
        self.assertIsNone(soup.select_one("[data-bookmarks-total]"))
        # the empty state is visible
        # note: text inside a Turbo Stream <template> is not exposed by
        # BeautifulSoup's get_text(), so assert on the raw response
        self.assertIsNotNone(soup.select_one("div.empty"))
        self.assertIn("You have no bookmarks yet", html)
        # update event is dispatched so the component can refresh the heading
        self.assertIn("bookmark-list-updated", html)
        # heading counter element persists in the DOM (not part of the stream)
        self.assertIsNone(soup.select_one("[data-bookmark-count]"))
