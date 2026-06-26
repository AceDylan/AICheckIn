import unittest

from app import _extract_by_path


class ExtractByPathTest(unittest.TestCase):
    def test_extracts_value_through_list_index(self):
        payload = {
            "data": {
                "items": [
                    {
                        "user": {
                            "balance": 85.79980481,
                        },
                    },
                ],
            },
        }

        self.assertEqual(
            _extract_by_path(payload, "data.items.0.user.balance"),
            85.79980481,
        )


if __name__ == "__main__":
    unittest.main()
