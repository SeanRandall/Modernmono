import tempfile
import unittest
import wave
from pathlib import Path

from tools.dictionary_extract import parse_dictionary
from tools.ne_extract import parse_ne
from tools.phonetics import parse
from tools.voice_inspect import inspect_voice, transition_units
from tools.render_units import (
    _compress_unit_correlated, _compress_unit_middle, _resize_unit,
    render, scheduled_events, selected_units,
)
from tools.engine import MonologEngine, UnknownWordError
from tools.text_to_phonetics import RulePronouncer
from tools.console_speak import text_with_inline_commands
from tools.import_ibmtts_dictionary import convert_spr


ROOT = Path(__file__).resolve().parents[1]


class DictionaryTests(unittest.TestCase):
    def test_default_dictionary(self):
        parsed = parse_dictionary(ROOT / "monologue16" / "FB_DEFLT.DIC")
        self.assertEqual(parsed["entry_count"], 891)
        self.assertEqual(parsed["strings_offset"], 0x22D2)
        self.assertEqual(parsed["entries"][0]["spelling"], "ABSENCE")
        self.assertEqual(parsed["entries"][0]["phonetics"], "'AEbs-IXns")
        self.assertEqual(parsed["entries"][-1]["spelling"], "YOUTH")


class NETests(unittest.TestCase):
    def test_22khz_voice_resources(self):
        metadata, resources = parse_ne(ROOT / "monologue16" / "FB_22K16.DLL")
        pcmd = [record for record, _ in resources if record["type"] == "PCMD"]
        count_resource = next(blob for record, blob in resources if record["name"] == "NUMPCMRESOURCES")
        self.assertEqual(int.from_bytes(count_resource[:2], "little"), 403)
        self.assertEqual(len(pcmd), 403)
        self.assertEqual([record["id"] for record in pcmd], list(range(300, 703)))

    def test_11khz_voice_resources(self):
        _, resources = parse_ne(ROOT / "monologue16" / "FB_11K8.DLL")
        pcmd = [record for record, _ in resources if record["type"] == "PCMD"]
        self.assertEqual(len(pcmd), 98)
        self.assertEqual([record["id"] for record in pcmd], list(range(300, 398)))


class PhoneticParserTests(unittest.TestCase):
    def test_ibm_spr_conversion(self):
        self.assertEqual(convert_spr("`[.1Sa.0kIG]"), "SH'AAkIHNG")
        self.assertEqual(convert_spr("`[.1Tru]"), "THr'UW")
        self.assertIsNone(convert_spr("`[.1?N]"))

    def test_dictionary_corpus_is_accepted(self):
        dictionary = parse_dictionary(ROOT / "monologue16" / "FB_DEFLT.DIC")
        for entry in dictionary["entries"]:
            with self.subTest(spelling=entry["spelling"]):
                self.assertTrue(parse(entry["phonetics"]))

    def test_reduced_vowel_expansion(self):
        tokens = parse("'AEbs-IXns")
        self.assertEqual([token.value for token in tokens if token.name == "phoneme"], [5, 0x1B, 0x24, 2, 0x16, 0x24])

    def test_original_text_rules_and_exceptions(self):
        pronouncer = RulePronouncer(ROOT / "monologue16" / "FB_22K16.DLL")
        expected = {
            "about": "-AXb'AWt",
            "although": "-AALXDH'OW",
            "hello": "h'EHl-OW",
            "through": "THrUW",
        }
        for word, phonetics in expected.items():
            with self.subTest(word=word):
                self.assertEqual(pronouncer.pronounce(word), phonetics)
                self.assertTrue(parse(phonetics))

    def test_rule_context_operators_select_digraphs(self):
        pronouncer = RulePronouncer(ROOT / "monologue16" / "FB_22K16.DLL")
        self.assertEqual(pronouncer.pronounce("thing"), "TH'IHNG")
        self.assertEqual(pronouncer.pronounce("this"), "DH'IHs")
        self.assertEqual(pronouncer.pronounce("singing"), "s'IHNG-IXNG")


class VoiceTests(unittest.TestCase):
    def test_22khz_unit_references(self):
        manifest, _ = inspect_voice(ROOT / "monologue16" / "FB_22K16.DLL")
        self.assertEqual(manifest["sample_rate"], 22050)
        self.assertEqual(manifest["bits_per_sample"], 16)
        self.assertEqual(manifest["phoneme_count"], 46)
        self.assertEqual(manifest["pcmd_resource_count"], 403)
        self.assertEqual(manifest["unit_count"], 3648)
        for matrix in ("a", "b"):
            for left in range(46):
                for right in range(46):
                    transition_units(manifest, left, right, matrix)

    def test_11khz_unit_references(self):
        manifest, _ = inspect_voice(ROOT / "monologue16" / "FB_11K8.DLL")
        self.assertEqual(manifest["sample_rate"], 11025)
        self.assertEqual(manifest["bits_per_sample"], 8)
        self.assertEqual(manifest["phoneme_count"], 46)

    def test_dictionary_pronunciation_selects_units(self):
        manifest, _ = inspect_voice(ROOT / "monologue16" / "FB_22K16.DLL")
        self.assertTrue(selected_units(manifest, "'AEbs-IXns"))

    def test_stress_attaches_contours(self):
        manifest, _ = inspect_voice(ROOT / "monologue16" / "FB_22K16.DLL")
        plain = scheduled_events(manifest, "AEbsIHns")
        stressed = scheduled_events(manifest, "'AEbs-IXns")
        self.assertEqual([event.unit_index for event in plain], [event.unit_index for event in stressed])
        self.assertTrue(any(event.duration_contour for event in stressed))
        self.assertTrue(any(event.period_offset for event in stressed))

    def test_inline_commands_attach_to_events(self):
        manifest, _ = inspect_voice(ROOT / "monologue16" / "FB_22K16.DLL")
        events = scheduled_events(manifest, "hEHP7S3V2D1lOW")
        delay = next(event for event in events if event.delay_units)
        after_delay = next(event for event in events[events.index(delay) + 1:] if event.unit_index is not None)
        self.assertEqual(after_delay.command_pitch, 7)
        self.assertEqual(after_delay.command_speed, 3)
        self.assertEqual(after_delay.volume, 2)

    def test_unit_resize(self):
        self.assertEqual(_resize_unit([1, 2, 3], 2, 2), [1, 2, 3, 3])

    def test_middle_compression_preserves_unit_boundaries(self):
        samples = list(range(200))
        compressed = _compress_unit_middle(samples, 50, 15)
        self.assertLess(len(compressed), len(samples))
        self.assertEqual(samples[:15], compressed[:15])
        self.assertEqual(samples[-15:], compressed[-15:])
        self.assertEqual(sorted(compressed), compressed)

        # The first non-zero setting must not duplicate/jump backwards at its
        # splice, which was the audible failure at Clear 56 / Maximum 45.
        onset = _compress_unit_middle(samples, 1, 15)
        self.assertEqual(sorted(onset), onset)
        self.assertEqual(len(samples) - 1, len(onset))

    def test_protected_rate_compression_shortens_speech(self):
        engine = MonologEngine(ROOT / "monologue16" / "FB_22K16.DLL", ROOT / "monologue16" / "FB_DEFLT.DIC")
        phonetics = engine.text_to_phonetics("files reports awards and menus")
        ordinary = engine.render_phonetics(phonetics, speed=18)
        boosted = engine.render_phonetics(phonetics, speed=18, unit_compression=50)
        self.assertLess(boosted.frame_count, ordinary.frame_count)

    def test_correlated_compression_preserves_edges_and_duration(self):
        samples = [((index % 20) - 10) * 1000 for index in range(240)]
        compressed = _compress_unit_correlated(samples, 40, 15)
        self.assertLess(len(compressed), len(samples))
        self.assertEqual(samples[:15], compressed[:15])
        self.assertEqual(samples[-15:], compressed[-15:])

        engine = MonologEngine(ROOT / "monologue16" / "FB_22K16.DLL", ROOT / "monologue16" / "FB_DEFLT.DIC")
        phonetics = engine.text_to_phonetics("files reports awards and menus")
        ordinary = engine.render_phonetics(phonetics, speed=18)
        aligned = engine.render_phonetics(
            phonetics, speed=18, unit_compression=50,
            compression_method="correlation",
        )
        self.assertLess(aligned.frame_count, ordinary.frame_count)

    def test_pause_compression_shortens_only_delays(self):
        engine = MonologEngine(ROOT / "monologue16" / "FB_22K16.DLL", ROOT / "monologue16" / "FB_DEFLT.DIC")
        phonetics = "hEHlOWD5"
        ordinary = engine.render_phonetics(phonetics, speed=13)
        shortened = engine.render_phonetics(phonetics, speed=13, pause_compression=75)
        self.assertLess(shortened.frame_count, ordinary.frame_count)
        shortened = _resize_unit(list(range(100)), -40, 2)
        self.assertEqual(len(shortened), 80)
        self.assertEqual(shortened[0], 0)

    def test_scheduled_render(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.wav"
            render(ROOT / "monologue16" / "FB_22K16.DLL", "hEHl'OW", output, mode="scheduled")
            with wave.open(str(output), "rb") as stream:
                self.assertEqual(stream.getframerate(), 22050)
                self.assertEqual(stream.getsampwidth(), 2)
                self.assertGreater(stream.getnframes(), 0)

    def test_speed_changes_scheduled_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            slow = Path(directory) / "slow.wav"
            fast = Path(directory) / "fast.wav"
            voice = ROOT / "monologue16" / "FB_22K16.DLL"
            render(voice, "hEHl'OW", slow, mode="scheduled", speed=3)
            render(voice, "hEHl'OW", fast, mode="scheduled", speed=7)
            with wave.open(str(slow), "rb") as slow_stream, wave.open(str(fast), "rb") as fast_stream:
                self.assertGreater(slow_stream.getnframes(), fast_stream.getnframes())


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = MonologEngine(
            ROOT / "monologue16" / "FB_22K16.DLL",
            ROOT / "monologue16" / "FB_DEFLT.DIC",
        )

    def test_multiword_phonetics(self):
        phonetics = self.engine.text_to_phonetics("absence, actually another.")
        self.assertIn("D1", phonetics)
        self.assertIn("|", phonetics)
        self.assertTrue(phonetics.endswith("D2"))

    def test_11khz_engine_renders_unsigned_8bit_audio(self):
        engine = MonologEngine(
            ROOT / "monologue16" / "FB_11K8.DLL",
            ROOT / "monologue16" / "FB_DEFLT.DIC",
        )
        audio = engine.render_text("hello world")
        self.assertEqual(11025, audio.sample_rate)
        self.assertEqual(1, audio.sample_width)
        self.assertTrue(audio.pcm)

    def test_excitation_scales_sentence_contours(self):
        phonetics = self.engine.text_to_phonetics("hello world!")
        flat = self.engine.render_phonetics(phonetics, excitation=0).pcm
        native = self.engine.render_phonetics(phonetics, excitation=50).pcm
        dynamic = self.engine.render_phonetics(phonetics, excitation=100).pcm
        self.assertNotEqual(flat, native)
        self.assertNotEqual(native, dynamic)

    def test_community_dictionary_phonetics_and_expansion(self):
        engine = MonologEngine(
            ROOT / "monologue16" / "FB_22K16.DLL",
            ROOT / "monologue16" / "FB_DEFLT.DIC",
            ROOT / "addon" / "data" / "IBMTTS_ENU.tsv",
            None,
            ROOT / "addon" / "data" / "CMUdict.tsv",
        )
        original = engine.text_to_phonetics("unicode")
        unicode_phonetics = engine.text_to_phonetics("unicode", use_community_dictionary=True)
        self.assertNotEqual(original, unicode_phonetics)
        corrected = {
            word: engine.text_to_phonetics(word, use_community_dictionary=True)
            for word in ("long", "pause", "because", "sentence", "short", "north", "order")
        }
        self.assertTrue(corrected["long"].startswith("l'AANG"))
        self.assertTrue(corrected["pause"].startswith("p'AAz"))
        self.assertTrue(corrected["because"].startswith("bIHk'AAz"))
        self.assertTrue(corrected["sentence"].startswith("s'EHntIXns"))
        self.assertTrue(corrected["short"].startswith("SH'OWrt"))
        self.assertTrue(corrected["north"].startswith("n'OWrTH"))
        self.assertTrue(corrected["order"].startswith("'OWrdER"))
        self.assertIn("y'UWnIXk\"OWd", unicode_phonetics)
        # Uppercase acronym expansions must not capture ordinary lowercase
        # words such as the community dictionary's special WITH entry.
        self.assertNotIn(
            "d'AHblIYUW",
            engine.text_to_phonetics("with", use_community_dictionary=True),
        )
        self.assertNotEqual(
            engine.text_to_phonetics("WITH"),
            engine.text_to_phonetics("WITH", use_community_dictionary=True),
        )
        for word, expected in {
            "tidy": "t'AYdIY",
            "menu": "m'EHnyUW",
            "undergone": '"AHndERg\'AAn',
            "etiquette": "'EHtIXkIXt",
            "intrigue": '"IHntr\'IYg',
        }.items():
            with self.subTest(cmu_word=word):
                self.assertIn(
                    expected,
                    engine.text_to_phonetics(word, use_community_dictionary=True),
                )

    def test_engine_user_phonetic_dictionary(self):
        with tempfile.TemporaryDirectory() as directory:
            user_dictionary = Path(directory) / "modernmono-user-dictionary.tsv"
            user_dictionary.write_text(
                "# spelling<TAB>phonetics\nhello\thEHl'OW\nhello world\thEHl'OW|w'ERLXd\n",
                encoding="utf-8",
            )
            engine = MonologEngine(
                ROOT / "monologue16" / "FB_22K16.DLL",
                ROOT / "monologue16" / "FB_DEFLT.DIC",
                None,
                user_dictionary,
            )
            self.assertEqual("hEHl'OW", engine.text_to_phonetics("hello"))
            self.assertEqual("hEHl'OW|w'ERLXd", engine.text_to_phonetics("HELLO WORLD"))
            engine.set_user_phonetics("test", "t'EHst")
            self.assertEqual("t'EHst", engine.text_to_phonetics("test"))
            engine.set_user_phonetics("hello", None)
            self.assertNotEqual("hEHl'OW", engine.text_to_phonetics("hello"))
            self.assertIn("test\tt'EHst", user_dictionary.read_text(encoding="utf-8"))
        nvda = engine.text_to_phonetics("NVDA", use_community_dictionary=True)
        self.assertTrue(parse(nvda))
        self.assertGreater(nvda.count("|"), 2)

    def test_multiword_render(self):
        audio = self.engine.render_text("absence actually another dictionary")
        self.assertEqual(audio.sample_rate, 22050)
        self.assertEqual(audio.sample_width, 2)
        self.assertGreater(audio.frame_count, 22050)

    def test_unknown_word_uses_original_rules(self):
        phonetics = self.engine.text_to_phonetics("foobarbaz")
        self.assertTrue(phonetics)
        self.assertTrue(parse(phonetics))

    def test_mixed_text_and_number_is_supported(self):
        phonetics = self.engine.text_to_phonetics("abc123")
        self.assertTrue(parse(phonetics))

    def test_console_inline_commands(self):
        phonetics = text_with_inline_commands(
            self.engine, "hello [[P8]]world [[D2]]through"
        )
        self.assertIn("P8", phonetics)
        self.assertIn("D2", phonetics)
        self.assertTrue(parse(phonetics))

    def test_punctuation_contours_and_pauses(self):
        statement = self.engine.text_to_phonetics("hello world.")
        question = self.engine.text_to_phonetics("hello world?")
        exclamation = self.engine.text_to_phonetics("hello world!")
        self.assertIn("30", statement)
        self.assertIn("0D2", statement)
        self.assertIn("70", question)
        self.assertIn("90", question)
        self.assertNotEqual(statement, exclamation)
        self.assertTrue(statement.endswith("D2"))
        self.assertTrue(question.endswith("D2"))
        for phonetics in (statement, question, exclamation):
            self.assertTrue(parse(phonetics))

    def test_spoken_operators_and_symbols(self):
        phonetics = self.engine.text_to_phonetics(
            "a # b % c & d * e @ f; a <= b <> c >= d := e"
        )
        self.assertIn("n'AHmbER", phonetics)
        self.assertIn("p-ERs'EHnt", phonetics)
        self.assertGreater(phonetics.count("D2"), 0)
        self.assertTrue(parse(phonetics))

    def test_compound_hyphen_is_not_spoken_as_minus(self):
        compound = self.engine.text_to_phonetics("well-known")
        explicit = self.engine.text_to_phonetics("well minus known")
        self.assertNotEqual(compound, explicit)

    def test_short_vowelless_token_is_spelled(self):
        phonetics = self.engine.text_to_phonetics("cm")
        self.assertIn("s'IY", phonetics)
        self.assertIn("'EHm", phonetics)
        self.assertIn("|", phonetics)

    def test_lowercase_indefinite_article_is_not_letter_name(self):
        article = self.engine.text_to_phonetics("a")
        letter = self.engine.text_to_phonetics("A")
        self.assertIn("-AX", article)
        self.assertIn("'EY", letter)
        self.assertNotEqual(article, letter)

    def test_contextual_the_reduction(self):
        alone = self.engine.text_to_phonetics("the")
        consonant = self.engine.text_to_phonetics("the thing")
        vowel = self.engine.text_to_phonetics("the other")
        self.assertIn("DH-IY", alone)
        self.assertIn("DH-AX", consonant)
        self.assertIn("DH-IY", vowel)

    def test_complete_boundary_rewrite_table(self):
        reduced_to = self.engine.text_to_phonetics("to London")
        self.assertIn("t-UH", reduced_to)
        flapped = self.engine.text_to_phonetics("get it")
        self.assertIn("DX", flapped)

    def test_delay_preserves_final_transition(self):
        manifest = self.engine.manifest
        plain = scheduled_events(manifest, "THr'IY")
        punctuated = scheduled_events(manifest, "THr'IY10D2")
        plain_units = [event.unit_index for event in plain if event.unit_index is not None]
        punctuated_units = [event.unit_index for event in punctuated if event.unit_index is not None]
        self.assertEqual(punctuated_units, plain_units)

    def test_abbreviations_and_acronym_heuristic(self):
        self.assertIn("m'IHsTX-ER", self.engine.text_to_phonetics("Mr."))
        acronym = self.engine.text_to_phonetics("IBM")
        self.assertIn("'AY", acronym)
        self.assertIn("b'IY", acronym)
        self.assertIn("'EHm", acronym)

    def test_numbers_ordinals_decimals_and_dates(self):
        for text in ("1,234", "21st", "3.14", "12/25/1999"):
            with self.subTest(text=text):
                self.assertTrue(parse(self.engine.text_to_phonetics(text)))

    def test_original_numeric_phonetic_tables(self):
        self.assertEqual("z'IHrOW10", self.engine.text_to_phonetics("0"))
        self.assertNotEqual(
            self.engine.text_to_phonetics("0"),
            self.engine.text_to_phonetics("zero"),
        )
        leading_zero = self.engine.text_to_phonetics("012")
        self.assertIn("z'IHrOW", leading_zero)
        self.assertIn("w'AHn", leading_zero)
        self.assertIn("t'UW", leading_zero)
        for value in range(90, 100):
            with self.subTest(value=value):
                phonetics = self.engine.text_to_phonetics(str(value))
                self.assertTrue(parse(phonetics))
                self.assertIn("n'AYndIY", phonetics)

    def test_currency(self):
        dollars = self.engine.text_to_phonetics("$1.25")
        self.assertIn("d'AAlER", dollars)
        self.assertIn("s'EHnt", dollars)

    def test_original_ordinal_and_month_tables(self):
        self.assertIn("f'ERst", self.engine.text_to_phonetics("1st"))
        self.assertIn("n'AYndIYTH", self.engine.text_to_phonetics("90th"))
        christmas = self.engine.text_to_phonetics("12/25/1999")
        self.assertIn("d-IXs'EHmbER", christmas)
        self.assertIn("f'IHfTH", christmas)

    def test_context_abbreviations_and_possessives(self):
        self.assertIn("sTXr'IYt", self.engine.text_to_phonetics("Main ST"))
        self.assertIn("sTXr'IYt", self.engine.text_to_phonetics("Main St."))
        self.assertIn("dZHr'AYv", self.engine.text_to_phonetics("Elm DR"))
        self.assertIn("m'AWntn", self.engine.text_to_phonetics("Rainier MT"))
        self.assertIn("z", self.engine.text_to_phonetics("Sean's"))

    def test_original_embedded_controls(self):
        controlled = self.engine.text_to_phonetics("<<P8 hello>> world")
        self.assertIn("P8", controlled)
        self.assertIn("P5", controlled)
        self.assertTrue(parse(controlled))
        self.assertEqual(self.engine.text_to_phonetics("<<~hEHl'OW>>"), "hEHl'OW")

    def test_character_spelling_mode(self):
        phonetics = self.engine.spell_to_phonetics("cm2")
        self.assertIn("s'IY", phonetics)
        self.assertIn("'EHm", phonetics)
        self.assertTrue(parse(phonetics))


if __name__ == "__main__":
    unittest.main()
