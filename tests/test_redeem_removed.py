import unittest

from app import app


class RedeemRemovedTest(unittest.TestCase):
    def test_home_page_has_no_redeem_ui_or_api_calls(self):
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('data-view="redeem"', html)
        self.assertNotIn('id="view-redeem"', html)
        self.assertNotIn("/api/redeem", html)
        self.assertNotIn("loadRedeem", html)

    def test_redeem_api_routes_are_removed(self):
        redeem_routes = sorted(
            rule.rule for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/api/redeem")
        )

        self.assertEqual(redeem_routes, [])


if __name__ == "__main__":
    unittest.main()
