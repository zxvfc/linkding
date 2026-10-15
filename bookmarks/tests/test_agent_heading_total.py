from django.test import TestCase
from django.urls import reverse

from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class AgentHeadingTotalTests(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    """Tests for the total bookmark counter next to the list page heading (R1-R3)."""

    def authenticate(self, items_per_page: int = None):
        user = self.get_or_create_test_user()
        if items_per_page is not None:
            profile = user.profile
            profile.items_per_page = items_per_page
            profile.save()
        self.client.force_login(user)
        return user

    def get_heading(self, response):
        soup = self.make_soup(response.content.decode())
        heading = soup.select_one("h1#main-heading")
        self.assertIsNotNone(heading, "Expected <h1 id=\"main-heading\"> to be rendered")
        return heading

    def get_counter(self, response):
        """Returns the counter element next to the heading, or None when not rendered."""
        soup = self.make_soup(response.content.decode())
        return soup.select_one('h1#main-heading span[aria-label$="bookmarks"]')

    # ------------------------------------------------------------------ #
    # R1: heading shows the total of the current selection, all pages
    # ------------------------------------------------------------------ #
    def test_r1_bookmarks_heading_shows_total_independent_of_pagination(self):
        self.authenticate(items_per_page=10)
        self.setup_numbered_bookmarks(42)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        heading = self.get_heading(response)

        self.assertIn("42", heading.get_text())

        # The full total is shown, not just the current pagination page
        soup = self.make_soup(response.content.decode())
        rendered_items = soup.select("ul.bookmark-list > li")
        self.assertEqual(10, len(rendered_items))

    def test_r1_archived_heading_shows_total_independent_of_pagination(self):
        self.authenticate(items_per_page=10)
        self.setup_numbered_bookmarks(42, archived=True)
        # Active bookmarks must not be counted on the archived page
        self.setup_numbered_bookmarks(7)

        response = self.client.get(reverse("linkding:bookmarks.archived"))
        heading = self.get_heading(response)

        self.assertIn("42", heading.get_text())
        self.assertNotIn("49", heading.get_text())

    def test_r1_shared_heading_shows_total_independent_of_pagination(self):
        self.authenticate(items_per_page=10)
        sharing_user = self.setup_user(enable_sharing=True)
        self.setup_numbered_bookmarks(42, shared=True, user=sharing_user)
        # Non-shared bookmarks must not be counted on the shared page
        self.setup_numbered_bookmarks(7, shared=False, user=sharing_user)

        response = self.client.get(reverse("linkding:bookmarks.shared"))
        heading = self.get_heading(response)

        self.assertIn("42", heading.get_text())
        self.assertNotIn("49", heading.get_text())

    def test_r1_heading_shows_total_matching_search_filter(self):
        self.authenticate(items_per_page=10)
        self.setup_numbered_bookmarks(20, prefix="foo")
        self.setup_numbered_bookmarks(30, prefix="bar")

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=foo")
        heading = self.get_heading(response)

        # Total of the filtered selection (20), not of all bookmarks (50)
        self.assertIn("20", heading.get_text())
        self.assertNotIn("50", heading.get_text())

    # ------------------------------------------------------------------ #
    # R2: no counter rendered when the selection is empty
    # ------------------------------------------------------------------ #
    def test_r2_no_counter_rendered_when_no_bookmarks(self):
        self.authenticate()

        response = self.client.get(reverse("linkding:bookmarks.index"))
        heading = self.get_heading(response)

        # No counter element next to the heading
        self.assertIsNone(self.get_counter(response))
        self.assertEqual("Bookmarks", heading.get_text(strip=True))
        # The existing empty state is shown instead
        soup = self.make_soup(response.content.decode())
        self.assertIsNotNone(soup.select_one(".empty"))

    def test_r2_no_counter_rendered_when_search_matches_nothing(self):
        self.authenticate()
        self.setup_numbered_bookmarks(5)

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=nomatch")
        heading = self.get_heading(response)

        self.assertIsNone(self.get_counter(response))
        self.assertEqual("Bookmarks", heading.get_text(strip=True))

    def test_r2_no_counter_rendered_on_archived_and_shared_when_empty(self):
        self.authenticate()
        urls = [
            reverse("linkding:bookmarks.archived"),
            reverse("linkding:bookmarks.shared"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                heading = self.get_heading(response)

                self.assertIsNone(self.get_counter(response))
                self.assertNotRegex(heading.get_text(strip=True), r"\d")

    # ------------------------------------------------------------------ #
    # R3: counter is accessible and uses existing muted utility classes
    # ------------------------------------------------------------------ #
    def test_r3_counter_is_accessible_and_visually_muted(self):
        self.authenticate(items_per_page=10)
        self.setup_numbered_bookmarks(42)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())
        counter = soup.select_one('h1#main-heading span[aria-label="42 bookmarks"]')

        self.assertIsNotNone(counter)
        # Visible text is the total
        self.assertEqual("42", counter.get_text(strip=True))
        # Existing muted/spacing utility classes are used
        self.assertIn("text-secondary", counter.get("class", []))
        self.assertIn("ml-1", counter.get("class", []))

    def test_r3_counter_is_rendered_inside_the_heading(self):
        self.authenticate()
        self.setup_bookmark()

        response = self.client.get(reverse("linkding:bookmarks.index"))
        heading = self.get_heading(response)
        counter = heading.select_one('span[aria-label="1 bookmarks"]')

        self.assertIsNotNone(counter)

    def test_r3_heading_labelledby_structure_remains_intact(self):
        self.authenticate()
        self.setup_bookmark()

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())

        main = soup.select_one('main[aria-labelledby="main-heading"]')
        self.assertIsNotNone(main)
        self.assertIsNotNone(soup.select_one('h1#main-heading'))
