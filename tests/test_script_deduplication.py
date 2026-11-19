import unittest
from unittest.mock import patch

from video_cli import (
    filter_continuation_duplicates,
    expand_narration,
    _normalize_words,
    _get_ngrams,
)


class ScriptDeduplicationTests(unittest.TestCase):
    def test_normalize_words_english_and_hindi(self):
        en_words = _normalize_words("Supermarkets use psychological tricks, right?")
        self.assertEqual(en_words, ["supermarkets", "use", "psychological", "tricks", "right"])

        hi_words = _normalize_words("सुपरमार्केट के अंदर, हर एक चीज सोची-समझी होती है।")
        self.assertIn("सुपरमार्केट", hi_words)
        self.assertIn("अंदर", hi_words)
        self.assertIn("चीज", hi_words)

    def test_get_ngrams(self):
        words = ["the", "silent", "tricks", "of", "supermarkets"]
        ngrams = _get_ngrams(words, n=4)
        self.assertEqual(len(ngrams), 2)
        self.assertIn(("the", "silent", "tricks", "of"), ngrams)
        self.assertIn(("silent", "tricks", "of", "supermarkets"), ngrams)

    def test_exact_sentence_duplicate_filtered(self):
        existing = "Supermarkets design their aisles to keep you browsing longer. The bakery scent stimulates appetite."
        continuation = "Supermarkets design their aisles to keep you browsing longer. Also, shoppers tend to turn right automatically."
        filtered = filter_continuation_duplicates(existing, continuation)
        self.assertNotIn("browsing longer", filtered)
        self.assertIn("shoppers tend to turn right automatically", filtered)

    def test_paraphrased_duplicate_sentence_filtered(self):
        existing = "Large shopping carts encourage customers to purchase significantly more items than planned."
        # Highly overlapping paraphrase
        continuation = (
            "Big shopping carts encourage customers to buy significantly more items than planned. "
            "Another trick is placing dairy and essentials at the furthest back corner of the store."
        )
        filtered = filter_continuation_duplicates(existing, continuation)
        self.assertNotIn("Big shopping carts", filtered)
        self.assertIn("furthest back corner", filtered)

    def test_ngram_overlap_filtered(self):
        existing = "Eye level is buy level which means the most expensive branded items are placed right at eye sight."
        continuation = (
            "Because eye level is buy level for expensive items at sight. "
            "Cheaper store-brand alternatives are consistently tucked away on the lowest shelves near the floor."
        )
        filtered = filter_continuation_duplicates(existing, continuation)
        self.assertNotIn("eye level is buy level for expensive items", filtered)
        self.assertIn("lowest shelves", filtered)

    def test_hindi_duplicate_sentences_filtered(self):
        existing = (
            "शॉपिंग कार्ट का बड़ा साइज आपको ज्यादा सामान खरीदने पर मजबूर करता है। "
            "आई लेवल पर हमेशा महंगे प्रोडक्ट्स रखे जाते हैं।"
        )
        continuation = (
            "शॉपिंग कार्ट का बड़ा साइज आपको ज्यादा सामान खरीदने पर मजबूर करता है। "
            "सस्ती चीजें हमेशा नीचे की शेल्फ पर रखी जाती हैं ताकि आप उन पर आसानी से ध्यान न दें।"
        )
        filtered = filter_continuation_duplicates(existing, continuation)
        self.assertNotIn("शॉपिंग कार्ट का बड़ा साइज", filtered)
        self.assertIn("सस्ती चीजें हमेशा नीचे की शेल्फ", filtered)

    def test_wholly_repetitive_continuation_rejected(self):
        existing = (
            "Supermarkets place milk at the back so you walk through everything. "
            "Relaxing background music slows down your walking speed. "
            "Checkout aisles are packed with impulse candy and chocolates."
        )
        continuation = (
            "Supermarkets place milk at the back so you walk through everything. "
            "Relaxing background music slows down your walking speed."
        )
        filtered = filter_continuation_duplicates(existing, continuation)
        self.assertEqual(filtered, "")

    def test_fresh_continuation_accepted(self):
        existing = "Supermarkets place milk at the very back of the store."
        continuation = (
            "Flooring tiles in expensive departments are often smaller to make your cart click faster. "
            "This subtle acoustic trick fools your brain into feeling you are rushing, so you naturally slow down."
        )
        filtered = filter_continuation_duplicates(existing, continuation)
        self.assertIn("Flooring tiles in expensive departments", filtered)
        self.assertIn("subtle acoustic trick", filtered)

    @patch("video_cli.gemini_json")
    def test_expand_narration_filters_redundant_response(self, mock_gemini):
        mock_gemini.return_value = {
            "continuation": "Supermarkets place milk at the back so you walk through everything."
        }
        project = {
            "language": "en",
            "content_narration": "Supermarkets place milk at the back so you walk through everything.",
            "narration": "Supermarkets place milk at the back so you walk through everything.",
        }
        result = expand_narration(project, 40)
        self.assertEqual(result, "")

    @patch("video_cli.gemini_json")
    def test_expand_narration_accepts_fresh_content(self, mock_gemini):
        mock_gemini.return_value = {
            "continuation": (
                "Supermarket background music usually plays around sixty beats per minute. "
                "This specific rhythm matches your resting heartbeat and prevents shoppers from feeling rushed."
            )
        }
        project = {
            "language": "en",
            "content_narration": "Milk is kept at the back wall of grocery stores.",
            "narration": "Milk is kept at the back wall of grocery stores.",
        }
        result = expand_narration(project, 40)
        self.assertIn("sixty beats per minute", result)
        self.assertIn("resting heartbeat", result)


if __name__ == "__main__":
    unittest.main()
