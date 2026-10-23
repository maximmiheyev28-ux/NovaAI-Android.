import unittest
from datetime import datetime, timedelta, timezone

from collect_fl_jobs import build_auto_package, is_fresh_for_auto_package, job_geo_eligibility, parse_wwr_feed, salary_label, score_item
from merge_jobs_feed import blocked_cached_item, too_old_to_carry


class CollectorPolicyTests(unittest.TestCase):
    def test_auto_package_only_for_dated_leads_within_72_hours(self):
        now = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
        self.assertTrue(is_fresh_for_auto_package({"publishedAt": "2026-10-22T12:00:00Z"}, now))
        self.assertFalse(is_fresh_for_auto_package({"publishedAt": "2026-10-19T12:00:00Z"}, now))
        self.assertFalse(is_fresh_for_auto_package({"publishedAt": ""}, now))
        self.assertFalse(is_fresh_for_auto_package({"publishedAt": "2026-10-24T12:00:00Z"}, now))

    def test_cloud_package_generates_non_claiming_reply_and_usable_plan(self):
        package = build_auto_package({
            "title": "Литературный редактор: фрагменты романа",
            "description": "Нужна литературная редактура нескольких фрагментов.",
            "link": "https://www.fl.ru/projects/123/example.html",
            "publishedAt": "2026-10-23T10:00:00Z",
            "budgetText": "",
            "locationText": "Уточнить доступность",
            "paymentCheck": "Способ выплаты не подтверждён.",
            "riskFlags": [],
            "score": 96,
        })
        self.assertEqual(package["category"], "текст / редактура")
        self.assertTrue(package["manualReviewRequired"])
        self.assertIn("Здравствуйте!", package["responseDraft"])
        self.assertIn("уточнить несколько деталей", package["responseDraft"])
        self.assertNotIn("многолетний опыт", package["responseDraft"].lower())
        self.assertIn("ПРОВЕРКА РЕЗУЛЬТАТА", package["workPlan"])
        self.assertIn("не подтверждён", package["workPlan"])
        self.assertGreaterEqual(len(package["steps"]), 4)
        self.assertGreaterEqual(len(package["questions"]), 3)

    def test_cloud_package_flags_desktop_heavy_title(self):
        package = build_auto_package({
            "title": "Добавить страницы в презентацию Figma",
            "description": "Нужны новые страницы в Figma.",
            "link": "https://www.fl.ru/projects/123/example.html",
            "publishedAt": "2026-10-23T10:00:00Z",
            "locationText": "Не указана",
            "paymentCheck": "Не подтверждено",
            "riskFlags": [],
            "score": 30,
        })
        self.assertIn("Высокий риск ограничения Android", package["androidAssessment"])
        self.assertIn("проверь нужные инструменты", package["workPlan"].lower())

    def test_we_work_remotely_feed_filters_explicit_local_only_roles_and_warns_for_unknown_geo(self):
        import collect_fl_jobs as collector

        xml = """<?xml version="1.0" encoding="UTF-8"?><rss><channel>
        <item><title>Remote Copywriter — US Only</title><link>https://weworkremotely.com/remote-jobs/example-us-only</link>
        <description>Write product content. Remote job - United States only.</description><pubDate>Fri, 23 Oct 2026 12:00:00 +0000</pubDate></item>
        <item><title>Worldwide Content Writer</title><link>https://weworkremotely.com/remote-jobs/example-worldwide</link>
        <description>Write blog articles. Work from anywhere in the world.</description><pubDate>Fri, 23 Oct 2026 12:00:00 +0000</pubDate></item>
        <item><title>Content Writer — USA</title><link>https://weworkremotely.com/remote-jobs/example-usa</link>
        <description>Write content for a blog.</description><pubDate>Fri, 23 Oct 2026 12:00:00 +0000</pubDate></item>
        <item><title>Remote Copywriter</title><link>https://weworkremotely.com/remote-jobs/example-unknown</link>
        <description>Write articles and edit content. We have a remote team.</description><pubDate>Fri, 23 Oct 2026 12:00:00 +0000</pubDate></item>
        </channel></rss>"""
        original = collector.read_url
        try:
            collector.read_url = lambda *args, **kwargs: xml.encode("utf-8")
            items = parse_wwr_feed()
        finally:
            collector.read_url = original
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["source"] == "We Work Remotely" for item in items))
        worldwide = next(item for item in items if "Worldwide" in item["title"])
        unknown = next(item for item in items if item["title"] == "Remote Copywriter")
        self.assertIn("Worldwide", worldwide["locationText"])
        self.assertIn("Страны найма не подтверждены", unknown["locationText"])
        self.assertIn("не подтверждена", unknown["paymentCheck"])
        self.assertTrue(all(item["link"].startswith("https://weworkremotely.com/") for item in items))

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

    def test_professional_design_software_is_not_android_first(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        figma_score = score_item(
            "Добавить пару страниц в готовую презентацию в Figma. Срочно (Бюджет: 2 000 ₽)",
            "Добавить страницы в Figma.",
            recent,
            2000,
        )[0]
        writing_score = score_item(
            "Литературный редактор",
            "Редактура текста и правка рукописи.",
            recent,
            0,
        )[0]
        self.assertLess(figma_score, 50)
        touch_score = score_item(
            "Презентация-викторина для touch-screen",
            "Интерактивная презентация для сенсорного экрана.",
            recent,
            0,
        )[0]
        self.assertLess(touch_score, 50)
        self.assertGreater(writing_score, figma_score)
        self.assertGreater(writing_score, touch_score)

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
        self.assertTrue(blocked_cached_item({"source": "FL.ru RSS", "title": "Добавить пару страниц в готовой презентации в Figma", "locationText": "Уточнить доступность"}))
        self.assertTrue(blocked_cached_item({"source": "FL.ru RSS", "title": "Презентация-викторина для touch-screen", "locationText": "Уточнить доступность"}))
        self.assertFalse(blocked_cached_item({"source": "Remote OK", "title": "Content writer", "locationText": "Worldwide"}))
        self.assertFalse(blocked_cached_item({"source": "FL.ru RSS", "title": "Литературный редактор", "locationText": "Уточнить доступность и условия у заказчика"}))


if __name__ == "__main__":
    unittest.main()
