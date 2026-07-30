from __future__ import annotations

import unittest

from app.domain.owner_truth.topic_shift_detection import (
    TOPIC_SHIFT_DETECTION_SCHEMA_VERSION,
    TopicShiftDetector,
    TopicShiftReasonCode,
)


class OwnerTruthTopicShiftDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = TopicShiftDetector()

    def test_explicit_topic_change_cues_are_detected_without_returning_text(self) -> None:
        for text in (
            "我们换个话题吧，聊聊我第一份工作。",
            "先不聊这个了，我想说说外婆的故事。",
            "我想聊点别的。",
            "Let's change the topic and talk about my school days.",
        ):
            with self.subTest(text=text):
                result = self.detector.detect(text)
                self.assertTrue(result.user_changed_topic)
                self.assertEqual(
                    result.reason_code,
                    TopicShiftReasonCode.EXPLICIT_TOPIC_CHANGE_CUE,
                )
                rendered = str(result.value_free_summary())
                self.assertNotIn(text, rendered)
                self.assertEqual(
                    result.value_free_summary()["schemaVersion"],
                    TOPIC_SHIFT_DETECTION_SCHEMA_VERSION,
                )

    def test_continuation_and_ambiguous_narrative_do_not_pause_a_thread(self) -> None:
        for text in (
            "我继续讲刚才院子里听故事的经历。",
            "换个时间我再把这段故事讲完。",
            "对了，我刚才提到的老师后来还联系过我。",
            "我想再补充一些当时的细节。",
        ):
            with self.subTest(text=text):
                result = self.detector.detect(text)
                self.assertFalse(result.user_changed_topic)
                self.assertEqual(
                    result.reason_code,
                    TopicShiftReasonCode.NO_EXPLICIT_TOPIC_CHANGE_CUE,
                )

    def test_non_string_input_is_rejected_without_coercion(self) -> None:
        with self.assertRaisesRegex(TypeError, "user_text must be a string"):
            self.detector.detect(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
