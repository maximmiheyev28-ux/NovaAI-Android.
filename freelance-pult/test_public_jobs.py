import unittest
from datetime import datetime, timedelta, timezone

from collect_public_jobs import build_auto_package, is_fresh_for_auto_package, job_geo_eligibility, parse_wwr_feed, salary_label, score_item
from collect_kwork import parse_kwork_html, parse_kwork_pages
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
        self.assertTrue(blocked_cached_item({"source": "Remote OK", "title": "Full-stack developer", "link": "https://remoteok.com/remote-jobs/a", "locationText": "Worldwide"}))
        self.assertTrue(blocked_cached_item({"source": "We Work Remotely", "title": "Добавить страницы в Figma", "link": "https://weworkremotely.com/remote-jobs/a", "locationText": "Уточнить доступность"}))
        self.assertTrue(blocked_cached_item({"source": "We Work Remotely", "title": "Презентация-викторина для touch-screen", "link": "https://weworkremotely.com/remote-jobs/b", "locationText": "Не указана"}))
        self.assertTrue(blocked_cached_item({"source": "Jobicy", "title": "Литературный редактор", "link": "https://jobicy.com/jobs/a", "locationText": "Worldwide"}))
        self.assertFalse(blocked_cached_item({"source": "Kwork", "title": "Перевод описаний", "link": "https://kwork.ru/projects/123/view", "locationText": "Kwork"}))


    def test_kwork_feed_only_recommends_tasks_with_a_deliverable(self):
        page = """<!doctype html><html><body>
        <div class="project-card"><a href="/projects/3157752/view"><h3>Перевод описаний товаров с английского</h3></a>
        <div>Нужно перевести 10 карточек и проверить термины.</div><div>Желаемый бюджет: до 3 000 ₽</div>
        <div>Осталось: 2 д. 5 ч.</div><div>Предложений: 2</div></div>
        <div class="project-card"><a href="/projects/3157753/view"><h3>Писать клиентам по готовому тексту</h3></a>
        <div>Нужно находить потенциальных клиентов и отправлять сообщения.</div>
        <div>Цена: 500 ₽</div><div>Осталось: 1 д. 2 ч.</div><div>Предложений: 1</div></div>
        <div><a href="https://jobicy.com/jobs/123/example">Удалённый редактор</a></div></body></html>"""
        payload = parse_kwork_html(page, checked_at="2026-10-24T00:00:00Z", previous={"items": []})
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["source"], "Kwork")
        self.assertEqual(item["link"], "https://kwork.ru/projects/3157752/view")
        self.assertEqual(item["recommendation"], "recommend")
        self.assertIn("текстовый результат", item["recommendationReason"].lower())
        self.assertEqual(item["budgetMax"], 3000)
        self.assertTrue(item["expiryAt"])
        self.assertTrue(item["firstSeenAt"])
        self.assertGreaterEqual(item["score"], 60)

    def test_kwork_recommends_common_copywriting_formats(self):
        page = """<!doctype html><html><body>
        <div class="project-card"><a href="/projects/3157770/view"><h3>Написать SEO-текст для страницы услуг</h3></a>
        <div>Подготовить оригинальный текст по техническому заданию.</div><div>Цена: 1200 ₽</div></div>
        <div class="project-card"><a href="/projects/3157771/view"><h3>Посты для Telegram-канала</h3></a>
        <div>Нужно подготовить публикации по контент-плану.</div><div>Цена: 900 ₽</div></div>
        <div class="project-card"><a href="/projects/3157772/view"><h3>Составить коммерческое предложение</h3></a>
        <div>Написать текст для предложения на основе материалов заказчика.</div><div>Цена: 1500 ₽</div></div>
        </body></html>"""
        payload = parse_kwork_html(page, checked_at="2026-10-24T00:00:00Z", previous={"items": []})
        self.assertEqual(len(payload["items"]), 3)
        self.assertTrue(all(item["executionCategory"] == "text" for item in payload["items"]))
        self.assertTrue(all(item["deliverable"] == "готовый текст" for item in payload["items"]))

    def test_kwork_parser_refuses_empty_page(self):
        with self.assertRaises(ValueError):
            parse_kwork_html("<html><body>No project cards here</body></html>")

    def test_kwork_combines_recommendations_from_five_pages(self):
        pages = []
        for number in range(1, 6):
            pages.append(f"""<!doctype html><html><body>
            <div class="project-card"><a href="/projects/315780{number}/view"><h3>Перевод описаний товаров {number}</h3></a>
            <div>Перевести описания карточек товаров с английского без потери смысла.</div>
            <div>Цена: 1000 ₽</div><div>Осталось: 1 д.</div></div>
            </body></html>""")
        payload = parse_kwork_pages(pages, checked_at="2026-10-24T00:00:00Z", previous={"items": []})
        self.assertEqual(len(payload["items"]), 5)
        self.assertEqual(payload["stats"]["fetchedUnique"], 5)
        self.assertEqual(payload["feeds"][0]["pages"], 5)
        self.assertEqual({item["kworkProjectId"] for item in payload["items"]},
                         {f"315780{number}" for number in range(1, 6)})

    def test_kwork_excludes_cad_drafting_but_allows_a_writable_greeting(self):
        page = """<!doctype html><html><body>
        <div class="project-card"><a href="/projects/3157760/view"><h3>Качественное поздравление на юбилей бабушки</h3></a>
        <div>Написать тёплое поздравление по вводным от заказчика.</div><div>Цена: 700 ₽</div><div>Осталось: 2 д.</div></div>
        <div class="project-card"><a href="/projects/3157761/view"><h3>Объединить DXF-файлы в один чертёж</h3></a>
        <div>Подготовить DXF и PDF, сохранить точные размеры, печать на плоттере.</div><div>Цена: 5000 ₽</div><div>Осталось: 2 д.</div></div>
        </body></html>"""
        payload = parse_kwork_html(page, checked_at="2026-10-24T00:00:00Z", previous={"items": []})
        self.assertEqual([item["title"] for item in payload["items"]],
                         ["Качественное поздравление на юбилей бабушки"])
        self.assertEqual(payload["stats"]["excludedReasons"].get("excluded_by_out_of_scope_filter"), 1)
        self.assertEqual(payload["stats"]["freshCandidates"], 1)


    def test_kwork_state_data_is_parsed_and_scored(self):
        page = '''<html><title>Биржа проектов - Kwork</title><script>
        window.stateData = {"wantsListData":{"wants":[
          {"id":456789,"name":"Перевод описаний товаров с английского",
           "description":"Нужно перевести 10 описаний карточек товаров и сохранить смысл.",
           "priceLimit":3000,"wantDates":{"dateCreate":"2026-10-23T23:30:00Z","dateActive":"2026-10-23T23:30:00Z"}}
        ],"pagination":{"last_page":31}}};
        </script></html>'''
        payload = parse_kwork_html(page, checked_at="2026-10-24T00:00:00Z", previous={"items": []})
        self.assertEqual(payload["source"], "Kwork public projects page")
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["id"], "kwork-456789")
        self.assertEqual(item["link"], "https://kwork.ru/projects/456789/view")
        self.assertEqual(item["title"], "Перевод описаний товаров с английского")
        self.assertEqual(item["budgetMax"], 3000)
        self.assertEqual(item["publishedAt"], "2026-10-23T23:30:00Z")
        self.assertEqual(item["recommendation"], "recommend")
        self.assertIn("текстовый результат", item["recommendationReason"].lower())


class TelegramNotificationPolicyTests(unittest.TestCase):
    def test_only_new_fresh_high_score_recommendations_from_kwork_are_selected(self):
        now = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
        previous = {"items": [{"link": "https://kwork.ru/projects/100/view", "source": "Kwork"}]}
        current = {"items": [
            {"link": "https://kwork.ru/projects/100/view", "source": "Kwork", "title": "Already known", "score": 90,
             "hasRelevantKeywords": True, "recommendation": "recommend", "criticalRisk": False,
             "firstSeenAt": "2026-10-23T20:00:00Z"},
            {"link": "https://kwork.ru/projects/101/view", "source": "Kwork", "title": "Fresh translator", "score": 86,
             "hasRelevantKeywords": True, "recommendation": "recommend", "criticalRisk": False,
             "firstSeenAt": "2026-10-23T23:00:00Z"},
            {"link": "https://kwork.ru/projects/102/view", "source": "Kwork", "title": "Not executable", "score": 99,
             "hasRelevantKeywords": True, "recommendation": "skip", "criticalRisk": False,
             "firstSeenAt": "2026-10-23T23:00:00Z"},
            {"link": "https://jobicy.com/jobs/103/a", "source": "Jobicy", "title": "Other site", "score": 99,
             "hasRelevantKeywords": True, "recommendation": "recommend", "criticalRisk": False,
             "publishedAt": "2026-10-23T23:00:00Z"},
        ]}
        selected = select_new_items(previous, current, now=now)
        self.assertEqual([item["title"] for item in selected], ["Fresh translator"])



if __name__ == "__main__":
    unittest.main()
