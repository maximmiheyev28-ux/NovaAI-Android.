import unittest
from datetime import datetime, timedelta, timezone

from collect_public_jobs import build_auto_package, is_fresh_for_auto_package, job_geo_eligibility, parse_wwr_feed, salary_label, score_item
from merge_jobs_feed import blocked_cached_item, remove_stale_auto_package, too_old_to_carry
from notify_telegram import select_new_items


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
            "link": "https://jobicy.com/jobs/123/example.html",
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

    def test_work_plan_does_not_require_phone_only(self):
        package = build_auto_package({
            "title": "Добавить страницы в презентацию Figma",
            "description": "Нужны новые страницы в Figma.",
            "link": "https://jobicy.com/jobs/123/example.html",
            "publishedAt": "2026-10-23T10:00:00Z",
            "locationText": "Не указана",
            "paymentCheck": "Не подтверждено",
            "riskFlags": [],
            "score": 70,
        })
        self.assertIn("устройства не ограничиваются телефоном", package["deviceAssessment"].lower())
        self.assertIn("устройства и инструменты", package["workPlan"].lower())


    def test_we_work_remotely_feed_filters_explicit_local_only_roles_and_warns_for_unknown_geo(self):
        import collect_public_jobs as collector

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

    def test_computer_required_work_is_scored_by_relevance_not_device(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        computer_task = score_item(
            "Редактирование презентации в Figma",
            "Нужна работа в Figma, правка презентации и оформление текста.",
            recent,
            0,
        )[0]
        writing_task = score_item(
            "Литературный редактор",
            "Редактура текста и правка рукописи.",
            recent,
            0,
        )[0]
        self.assertGreaterEqual(computer_task, 50)
        self.assertGreaterEqual(writing_task, 50)


    def test_software_specific_and_touchscreen_jobs_are_not_filtered_by_device(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        figma_score = score_item(
            "Добавить пару страниц в готовую презентацию в Figma. Срочно",
            "Добавить страницы в Figma.",
            recent,
            2000,
        )[0]
        touch_score = score_item(
            "Презентация-викторина для touch-screen",
            "Интерактивная презентация для сенсорного экрана.",
            recent,
            0,
        )[0]
        self.assertGreater(figma_score, 0)
        self.assertGreater(touch_score, 0)


    def test_zero_salary_is_not_reported_as_real_pay(self):
        self.assertEqual(salary_label({"minSalary": 0, "maxSalary": 0, "currency": "", "salaryPeriod": "annual"}), (0, 0, ""))

    def test_jobs_older_than_one_week_are_ranked_below_recent_relevant_work(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0)
        old = recent - timedelta(days=10)
        fresh_score = score_item("Литературный редактор", "Редактура текста и правка рукописи.", recent.isoformat().replace("+00:00", "Z"), 0)[0]
        old_score = score_item("Литературный редактор", "Редактура текста и правка рукописи.", old.isoformat().replace("+00:00", "Z"), 0)[0]
        self.assertGreater(fresh_score, old_score)
        self.assertLess(old_score, 50)

    def test_stale_cloud_packages_are_removed_but_fresh_ones_remain(self):
        now = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
        fresh = {"title": "Writer", "publishedAt": "2026-10-22T12:00:00Z", "autoPackage": {"category": "text"}}
        old = {"title": "Writer", "publishedAt": "2026-10-19T12:00:00Z", "autoPackage": {"category": "text"}}
        unknown = {"title": "Writer", "publishedAt": "", "autoPackage": {"category": "text"}}
        self.assertIn("autoPackage", remove_stale_auto_package(fresh, now))
        self.assertNotIn("autoPackage", remove_stale_auto_package(old, now))
        self.assertNotIn("autoPackage", remove_stale_auto_package(unknown, now))

    def test_cached_listings_expire_after_14_days_for_all_sources(self):
        now = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
        recent_listing = {"source": "We Work Remotely", "publishedAt": "2026-10-19T14:33:33Z"}
        older_listing = {"source": "Jobicy", "publishedAt": "2026-10-15T06:17:25Z"}
        too_old_listing = {"source": "Remote OK", "publishedAt": "2026-10-01T06:17:25Z"}
        self.assertFalse(too_old_to_carry(recent_listing, now))
        self.assertFalse(too_old_to_carry(older_listing, now))
        self.assertTrue(too_old_to_carry(too_old_listing, now))


    def test_removed_source_is_never_carried_from_old_snapshots(self):
        self.assertTrue(blocked_cached_item({
            "source": "FL.ru RSS",
            "title": "Литературный редактор",
            "link": "https://www.fl.ru/projects/123/example.html",
            "locationText": "Worldwide",
        }))
        self.assertTrue(blocked_cached_item({
            "source": "Unknown",
            "title": "Литературный редактор",
            "link": "https://sub.fl.ru/projects/123/example.html",
            "locationText": "Worldwide",
        }))

    def test_cached_country_restrictions_are_removed_but_device_requirements_are_not(self):
        self.assertTrue(blocked_cached_item({"source": "Jobicy", "title": "Content writer", "locationText": "UK"}))
        self.assertTrue(blocked_cached_item({"source": "Remote OK", "title": "Content writer", "locationText": "United States only"}))
        self.assertFalse(blocked_cached_item({"source": "Remote OK", "title": "Full-stack developer", "locationText": "Worldwide"}))
        self.assertFalse(blocked_cached_item({"source": "We Work Remotely", "title": "Добавить страницы в Figma", "locationText": "Уточнить доступность"}))
        self.assertFalse(blocked_cached_item({"source": "We Work Remotely", "title": "Презентация-викторина для touch-screen", "locationText": "Не указана"}))
        self.assertFalse(blocked_cached_item({"source": "Jobicy", "title": "Литературный редактор", "locationText": "Worldwide"}))


class TelegramNotificationPolicyTests(unittest.TestCase):
    def test_only_new_fresh_high_score_jobs_from_known_sources_are_selected(self):
        now = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
        previous = {"items": [
            {"link": "https://jobicy.com/jobs/old?utm_source=feed", "source": "Jobicy"}
        ]}
        current = {"items": [
            {"link": "https://jobicy.com/jobs/old", "source": "Jobicy", "title": "Already known", "score": 90,
             "hasRelevantKeywords": True, "criticalRisk": False, "publishedAt": "2026-10-23T10:00:00Z"},
            {"link": "https://remoteok.com/remote-jobs/new-task", "source": "Remote OK", "title": "Fresh editor",
             "score": 86, "hasRelevantKeywords": True, "criticalRisk": False, "publishedAt": "2026-10-23T10:00:00Z"},
            {"link": "https://remoteok.com/remote-jobs/old", "source": "Remote OK", "title": "Old editor",
             "score": 90, "hasRelevantKeywords": True, "criticalRisk": False, "publishedAt": "2026-10-01T10:00:00Z"},
            {"link": "https://jobicy.com/jobs/risk", "source": "Jobicy", "title": "Risky role", "score": 90,
             "hasRelevantKeywords": True, "criticalRisk": True, "publishedAt": "2026-10-23T10:00:00Z"},
            {"link": "https://kwork.ru/projects/123", "source": "Kwork", "title": "Not fetched automatically",
             "score": 99, "hasRelevantKeywords": True, "criticalRisk": False, "publishedAt": "2026-10-23T10:00:00Z"}
        ]}
        selected = select_new_items(previous, current, now=now)
        self.assertEqual([item["title"] for item in selected], ["Fresh editor"])


if __name__ == "__main__":
    unittest.main()
