import unittest

import ensure_secrets as subject

WANTED = {"app_auth_20261101_v1": "AUTH", "app_db_20261101_v2": "DB"}


class PlanTests(unittest.TestCase):
    def test_creates_only_what_is_missing(self):
        created = subject.plan(WANTED, {"app_auth_20261101_v1"}, {"DB": "value"})
        self.assertEqual(created, {"app_db_20261101_v2": "value"})

    def test_an_existing_secret_needs_no_value(self):
        self.assertEqual(subject.plan(WANTED, set(WANTED), {}), {})

    def test_a_missing_value_names_the_github_secret_not_the_value(self):
        with self.assertRaisesRegex(subject.SecretsError, "AUTH, DB"):
            subject.plan(WANTED, set(), {"DB": ""})

    def test_refuses_an_unversioned_name(self):
        with self.assertRaisesRegex(subject.SecretsError, "database_url"):
            subject.plan({"database_url": "DB"}, set(), {"DB": "value"})


if __name__ == "__main__":
    unittest.main()
