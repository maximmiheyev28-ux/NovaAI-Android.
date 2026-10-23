import unittest
from datetime import datetime, timedelta, timezone

from collect_fl_jobs import job_geo_eligibility, salary_label, score_item
from merge_jobs_feed import blocked_cached_item, too_old_to_carry


class CollectorPolicyTests(unittest.TestCase):
    def test_explicit_country_restrictions_are_excluded(self):
        for country in ("USA", "UK", "Canada", "Spain", "Australia", "LATAM, Bulgaria, Canada"):
            with self.subTest(country=country):
                allowed, _ = job_geo_eligibility(country)
                self.assertFalse(allowed)

    def test_worldwide_and_russia_are_candidates_with_warning_or_clear_scope(self):
        self.assertTrue(job_geo_eligibility("Worldwide")[0])
        self.assertTrue(job_geo_eligibility("Russia")[0])
        self.assertTrue(job_geo_eligibility("CIS")[0])
        europe_ok, europe_note = job_geo_eligibility("EMEA")
        self.assertTrue(europe_ok)
        self.assertIn("уточни", europe_note.lower())

    def test_unknown_geo_is_kept_but_flagged(self):
        allowed, note = job_geo_eligibility("")
        self.assertTrue(allowed)
        self.assertIn("не указана", note.lower())

    def test_computer_heavy_titles_rank_below_android_friendly_text_work(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        mobile_score = score_item("Литературный редактор", "Редактура текста и правка рукописи.", recent, 0)[0]
        technical_score = score_item("Конструкторская документация и чертёж", "Нужны чертежи и инженерная документация.", recent, 0)[0]
        self.assertGreater(mobile_score, technical_score)

    def test_zero_salary_is_not_reported_as_real_pay(self):
        self.assertEqual(salary_label({"minSalary": 0, "maxSalary": 0, "currency": "", "salaryPeriod": "annual"}), (0, 0, ""))

    def test_jobs_older_than_one_week_are_ranked_below_recent_android_friendly_work(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0)
        old = recent - timedelta(days=10)
        fresh_score = score_item("Литературный редактор", "Редактура текста и правка рукописи.", recent.isoformat().replace("+00:00", "Z"), 0)[0]
        old_score = score_item("Литературный редактор", "Редактура текста и правка рукописи.", old.isoformat().replace("+00:00", "Z"), 0)[0]
        self.assertGreater(fresh_score, old_score)
        self.assertLess(old_score, 50)

    def test_cached_project_staleness_window_is_shorter_than_remote_role_window(self):
        now = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
        old_project = {"source": "FL.ru RSS", "publishedAt": "2026-10-15T06:17:25Z"}
        recent_project = {"source": "FL.ru RSS", "publishedAt": "2026-10-19T14:33:33Z"}
        older_remote_role = {"source": "Jobicy", "publishedAt": "2026-10-15T06:17:25Z"}
        too_old_remote_role = {"source": "Jobicy", "publishedAt": "2026-10-01T06:17:25Z"}
        self.assertTrue(too_old_to_carry(old_project, now))
        self.assertFalse(too_old_to_carry(recent_project, now))
        self.assertFalse(too_old_to_carry(older_remote_role, now))
        self.assertTrue(too_old_to_carry(too_old_remote_role, now))

    def test_cached_country_specific_and_desktop_jobs_are_removed(self):
        self.assertTrue(blocked_cached_item({"source": "Jobicy", "title": "Content writer", "locationText": "UK"}))
        self.assertTrue(blocked_cached_item({"source": "Remote OK", "title": "Remote software developer", "locationText": "Worldwide"}))
        self.assertFalse(blocked_cached_item({"source": "Remote OK", "title": "Content writer", "locationText": "Worldwide"}))
        self.assertFalse(blocked_cached_item({"source": "FL.ru RSS", "title": "Литературный редактор", "locationText": "Уточнить доступность и условия у заказчика"}))


if __name__ == "__main__":
    unittest.main()
