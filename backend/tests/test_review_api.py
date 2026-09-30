"""复盘接口冒烟测试；不调用真实大模型，也不写入真实数据目录。"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import quiz as quiz_service
from backend.app.services import review as review_service


class ReviewApiSmokeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self._tmp.name)
        quiz_dir = data_dir / "quiz"
        self.review_file = quiz_dir / "review_schedule.json"
        self._originals = {
            "review": (
                review_service.REVIEW_FILE,
                review_service.SYNC_QUEUE_FILE,
                review_service.REVIEW_META_FILE,
            ),
            "quiz": (
                quiz_service.QUIZ_DIR,
                quiz_service.PAPERS_DIR,
                quiz_service.ATTEMPTS_DIR,
                quiz_service.MISTAKES_FILE,
                quiz_service.MASTERY_FILE,
                quiz_service.MASTERY_META_FILE,
                quiz_service.TOPICS_FILE,
            ),
        }
        review_service.REVIEW_FILE = self.review_file
        review_service.SYNC_QUEUE_FILE = quiz_dir / "review_sync_queue.json"
        review_service.REVIEW_META_FILE = quiz_dir / "review_meta.json"
        quiz_service.QUIZ_DIR = quiz_dir
        quiz_service.PAPERS_DIR = quiz_dir / "papers"
        quiz_service.ATTEMPTS_DIR = quiz_dir / "attempts"
        quiz_service.MISTAKES_FILE = quiz_dir / "mistakes.json"
        quiz_service.MASTERY_FILE = quiz_dir / "mastery.json"
        quiz_service.MASTERY_META_FILE = quiz_dir / "mastery_meta.json"
        quiz_service.TOPICS_FILE = quiz_dir / "topics.json"
        quiz_service.ensure_dirs()
        self.client = TestClient(app)

    def tearDown(self):
        (
            review_service.REVIEW_FILE,
            review_service.SYNC_QUEUE_FILE,
            review_service.REVIEW_META_FILE,
        ) = self._originals["review"]
        (
            quiz_service.QUIZ_DIR,
            quiz_service.PAPERS_DIR,
            quiz_service.ATTEMPTS_DIR,
            quiz_service.MISTAKES_FILE,
            quiz_service.MASTERY_FILE,
            quiz_service.MASTERY_META_FILE,
            quiz_service.TOPICS_FILE,
        ) = self._originals["quiz"]
        self._tmp.cleanup()

    @staticmethod
    def _mistake():
        stem = "API 自动化断言应该校验什么？"
        return {
            "key": stem[:120],
            "topic": "接口自动化",
            "type": "single",
            "stem": stem,
            "options": [
                {"key": "A", "text": "只校验 HTTP 200"},
                {"key": "B", "text": "状态码、关键业务字段和契约"},
            ],
            "correct_answer": ["B"],
            "correct_answer_text": "B",
            "user_answer_text": "A",
            "explanation": "接口断言要覆盖业务结果，而不是只看状态码。",
            "source_title": "接口测试讲义",
            "wrong_count": 1,
            "created_at": int(time.time()) - 86400,
            "last_seen_at": int(time.time()) - 86400,
        }

    def test_health_dashboard_records_error_cause_and_practice_api(self):
        mistake = self._mistake()
        quiz_service.MISTAKES_FILE.write_text(
            json.dumps([mistake], ensure_ascii=False), encoding="utf-8"
        )

        health = self.client.get("/api/health")
        dashboard = self.client.get("/api/quiz/review/dashboard")
        records = self.client.get("/api/quiz/review/mistakes")
        knowledge_map = self.client.get("/api/quiz/review/knowledge-map")

        self.assertEqual(200, health.status_code)
        self.assertEqual(200, dashboard.status_code)
        self.assertEqual(200, records.status_code)
        self.assertEqual(200, knowledge_map.status_code)
        self.assertEqual(1, dashboard.json()["due_count"])
        self.assertEqual(1, len(records.json()["records"]))

        review_id = records.json()["records"][0]["review_id"]
        saved = self.client.put(
            f"/api/quiz/review/{review_id}/error-causes",
            json={"cause": "概念混淆"},
        )
        self.assertEqual(200, saved.status_code)
        self.assertEqual(["概念混淆"], saved.json()["record"]["error_causes"])

        fake_paper = {
            "paper_id": "paper_api_review",
            "title": "复盘练习",
            "questions": [],
            "generation_context": {
                "kind": "review_practice",
                "topic_id": "接口自动化",
                "review_ids": [review_id],
            },
        }
        with (
            patch.object(
                review_service,
                "generate_practice_paper",
                return_value=fake_paper,
            ),
            patch.object(
                quiz_service,
                "get_paper",
                return_value=fake_paper,
            ),
        ):
            practice = self.client.post("/api/quiz/review/tasks/%E6%8E%A5%E5%8F%A3%E8%87%AA%E5%8A%A8%E5%8C%96/practice")

        self.assertEqual(200, practice.status_code)
        self.assertEqual("review_practice", practice.json()["generation_context"]["kind"])

    def test_sync_retry_endpoint(self):
        retry = self.client.post("/api/quiz/review/sync-retry")

        self.assertEqual(200, retry.status_code)
        self.assertEqual(0, retry.json()["retried"])
        self.assertEqual(0, retry.json()["pending_count"])


if __name__ == "__main__":
    unittest.main()
