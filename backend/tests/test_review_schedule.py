"""错题复盘调度回归测试；全部使用临时数据目录。"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import quiz as quiz_service
from backend.app.services import review as review_service


class ReviewScheduleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        quiz_dir = self.data_dir / "quiz"
        self.review_file = quiz_dir / "review_schedule.json"
        self._originals = {
            "review": (review_service.REVIEW_FILE,),
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
        quiz_service.QUIZ_DIR = quiz_dir
        quiz_service.PAPERS_DIR = quiz_dir / "papers"
        quiz_service.ATTEMPTS_DIR = quiz_dir / "attempts"
        quiz_service.MISTAKES_FILE = quiz_dir / "mistakes.json"
        quiz_service.MASTERY_FILE = quiz_dir / "mastery.json"
        quiz_service.MASTERY_META_FILE = quiz_dir / "mastery_meta.json"
        quiz_service.TOPICS_FILE = quiz_dir / "topics.json"
        quiz_service.ensure_dirs()

    def tearDown(self):
        (
            review_service.REVIEW_FILE,
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
    def _question(topic="RAG 分块", stem="Chunk Overlap 的作用是什么？", correct="B"):
        return {
            "topic": topic,
            "type": "single",
            "stem": stem,
            "options": [
                {"key": "A", "text": "加快检索"},
                {"key": "B", "text": "避免上下文被边界切断"},
            ],
            "correct_answer": [correct],
            "correct_answer_text": correct,
            "user_answer": ["A"],
            "user_answer_text": "A",
            "is_correct": False,
            "explanation": "Overlap 让相邻块保留重叠内容。",
            "source_title": "RAG 讲义",
            "source_doc_id": "doc_rag",
            "source_docs": [{"doc_id": "doc_rag", "title": "RAG 讲义"}],
        }

    def _paper(self, questions, context=None):
        return {
            "paper_id": "paper_test",
            "title": "测试卷",
            "doc_ids": ["doc_rag"],
            "doc_titles": ["RAG 讲义"],
            "source_docs": [{"doc_id": "doc_rag", "title": "RAG 讲义"}],
            "questions": questions,
            "generation_context": context or {},
        }

    def test_legacy_mistakes_are_migrated_without_loss(self):
        mistake = {
            **self._question(),
            "key": self._question()["stem"][:120],
            "wrong_count": 2,
            "created_at": int(time.time()) - 86400,
            "last_seen_at": int(time.time()) - 86400,
        }
        quiz_service.MISTAKES_FILE.write_text(
            json.dumps([mistake], ensure_ascii=False), encoding="utf-8"
        )

        created = review_service.migrate_legacy_mistakes()
        records = review_service.list_records()
        remaining = quiz_service.list_mistakes()

        self.assertEqual(1, created)
        self.assertEqual(1, len(records))
        self.assertEqual(mistake["key"], records[0]["question_key"])
        self.assertEqual("doc_rag", records[0]["source_lineage"]["source_doc_id"])
        self.assertEqual(0, records[0]["next_review_at"])
        self.assertEqual(1, len(remaining))

    def test_wrong_answer_schedules_and_correct_answer_keeps_schedule(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        record = review_service.list_records()[0]

        self.assertEqual("active", record["status"])
        self.assertEqual(0, record["review_stage"])
        self.assertGreater(record["next_review_at"], int(time.time()))
        self.assertEqual(1, record["wrong_count"])

        correct = dict(question)
        correct.update({"is_correct": True, "user_answer": ["B"], "user_answer_text": "B"})
        review_service.sync_wrong_answers([correct], self._paper([correct]))
        unchanged = review_service.list_records()[0]

        self.assertEqual(record["review_stage"], unchanged["review_stage"])
        self.assertEqual(record["next_review_at"], unchanged["next_review_at"])

    def test_review_practice_pass_advances_and_failure_resets(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        review_id = review_service.list_records()[0]["review_id"]
        review_service.REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = json.loads(review_service.REVIEW_FILE.read_text(encoding="utf-8"))
        state["records"][0]["next_review_at"] = 0
        review_service.REVIEW_FILE.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

        correct = dict(question)
        correct.update({"is_correct": True, "user_answer": ["B"], "user_answer_text": "B"})
        context = {
            "kind": "review_practice",
            "review_ids": [review_id],
        }
        result = review_service.sync_grade([correct], self._paper([correct], context))
        advanced = review_service.list_records()[0]

        self.assertEqual({"passed": True, "review_count": 1}, result)
        self.assertEqual(1, advanced["review_stage"])
        self.assertEqual(1, advanced["review_pass_count"])
        self.assertGreater(advanced["next_review_at"], int(time.time()))

        wrong = dict(question)
        wrong.update({"user_answer": ["A"], "user_answer_text": "A"})
        review_service.sync_grade([wrong], self._paper([wrong], context))
        reset = review_service.list_records()[0]

        self.assertEqual(0, reset["review_stage"])
        self.assertEqual(0, reset["review_pass_count"])
        self.assertEqual("active", reset["status"])

    def test_four_passes_graduate_record(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        review_id = review_service.list_records()[0]["review_id"]
        context = {"kind": "review_practice", "review_ids": [review_id]}
        correct = dict(question)
        correct.update({"is_correct": True, "user_answer": ["B"], "user_answer_text": "B"})

        for expected_passes in range(1, 5):
            state = json.loads(review_service.REVIEW_FILE.read_text(encoding="utf-8"))
            state["records"][0]["next_review_at"] = 0
            review_service.REVIEW_FILE.write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            review_service.sync_grade([correct], self._paper([correct], context))
            record = review_service.list_records()[0]
            self.assertEqual(expected_passes, record["review_pass_count"])

        self.assertEqual("graduated", record["status"])
        self.assertEqual(0, record["next_review_at"])

    def test_manual_delete_archives_without_removing_history(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        key = question["stem"][:120]
        quiz_service.MISTAKES_FILE.write_text(
            json.dumps([{**question, "key": key}], ensure_ascii=False),
            encoding="utf-8",
        )

        quiz_service.delete_mistake(key)
        records = review_service.list_records()

        self.assertEqual([], quiz_service.list_mistakes())
        self.assertEqual("archived", records[0]["status"])
        self.assertTrue(records[0]["question_key"])

    def test_practice_paper_binds_due_records(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        state = json.loads(review_service.REVIEW_FILE.read_text(encoding="utf-8"))
        state["records"][0]["next_review_at"] = 0
        review_service.REVIEW_FILE.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        fake_paper = {
            **self._paper([question]),
            "paper_id": "generated_review_paper",
        }
        with (
            patch(
                "backend.app.services.knowledge.list_documents",
                return_value=[{"doc_id": "doc_rag", "title": "RAG 讲义"}],
            ),
            patch(
                "backend.app.services.quiz.generate_paper",
                return_value=fake_paper,
            ) as fake_generate,
        ):
            paper = review_service.generate_practice_paper("rag分块")

        self.assertEqual("generated_review_paper", paper["paper_id"])
        self.assertEqual("review_practice", paper["generation_context"]["kind"])
        self.assertEqual(1, len(paper["generation_context"]["review_ids"]))
        fake_generate.assert_called_once()
        self.assertTrue(
            (quiz_service.PAPERS_DIR / "generated_review_paper.json").exists()
        )

    def test_error_cause_is_validated_and_saved(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        review_id = review_service.list_records()[0]["review_id"]

        record = review_service.update_error_cause(review_id, "概念不清")
        with self.assertRaises(review_service.ReviewError):
            review_service.update_error_cause(review_id, "不存在的错因")

        self.assertEqual(["概念不清"], record["error_causes"])


if __name__ == "__main__":
    unittest.main()
