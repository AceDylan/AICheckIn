import unittest

from app import app


class BookmarkFirstUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_bookmarks_are_the_default_workspace(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Bookmark Hub · AI 服务工作台", html)
        self.assertIn('class="tab active" data-view="bookmarks"', html)
        self.assertIn('class="view active" id="view-bookmarks"', html)
        self.assertNotIn('class="view active" id="view-checkin"', html)
        self.assertIn('id="bmSearch"', html)
        self.assertIn('id="refreshAllBm"', html)
        self.assertIn('id="bmNavCount"', html)

    def test_modern_stylesheet_is_served(self):
        response = self.client.get("/static/app-v3.css")
        self.addCleanup(response.close)
        css = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(".app-shell", css)
        self.assertIn(".bookmark-grid", css)
        self.assertIn(".link-grid", css)
        self.assertIn("@media (max-width: 760px)", css)

    def test_existing_bookmark_and_checkin_api_bindings_remain(self):
        html = self.client.get("/").get_data(as_text=True)

        for endpoint in (
            "/api/bookmarks",
            "/refresh_balance",
            "/api/checkin",
            "/api/configs",
            "/api/history",
        ):
            self.assertIn(endpoint, html)


if __name__ == "__main__":
    unittest.main()
