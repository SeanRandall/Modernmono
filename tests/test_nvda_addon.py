import importlib.util
import struct
import sys
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Action:
    def __init__(self):
        self.calls = []

    def notify(self, **kwargs):
        self.calls.append(kwargs)


class _BaseDriver:
    @classmethod
    def VoiceSetting(cls): return "voice"

    @classmethod
    def RateSetting(cls): return "rate"

    @classmethod
    def RateBoostSetting(cls): return "rateBoost"

    @classmethod
    def PitchSetting(cls): return "pitch"

    @classmethod
    def VolumeSetting(cls): return "volume"

    def terminate(self): pass


class _IndexCommand:
    def __init__(self, index): self.index = index


class _CharacterModeCommand:
    def __init__(self, state): self.state = state


class _BreakCommand:
    def __init__(self, time=0): self.time = time


class _ParamCommand:
    def __init__(self, value): self.newValue = value


class _WavePlayer:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.chunks = []
        self.stopped = False

    def feed(self, data, onDone=None):
        self.chunks.append(data)
        if onDone: onDone()

    def idle(self): pass
    def sync(self): pass
    def stop(self): self.stopped = True
    def pause(self, switch): pass
    def close(self): pass
    def setVolume(self, **kwargs): pass


class NvdaAddonTests(unittest.TestCase):
    def test_packaged_engine_uses_package_relative_imports(self):
        engine_source = (ROOT / "addon" / "synthDrivers" / "_modernmono" / "engine.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("from tools.", engine_source)
        for module in ("dictionary_extract", "phonetics", "render_units", "text_to_phonetics", "voice_inspect"):
            self.assertIn(f"from .{module} import", engine_source)

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "addon"))
        cls.indexAction = _Action()
        cls.doneAction = _Action()

        commands = types.ModuleType("speech.commands")
        commands.IndexCommand = _IndexCommand
        commands.CharacterModeCommand = _CharacterModeCommand
        commands.BreakCommand = _BreakCommand
        commands.PitchCommand = type("PitchCommand", (_ParamCommand,), {})
        commands.RateCommand = type("RateCommand", (_ParamCommand,), {})
        commands.VolumeCommand = type("VolumeCommand", (_ParamCommand,), {})
        speech = types.ModuleType("speech")
        speech.commands = commands
        sys.modules["speech"] = speech
        sys.modules["speech.commands"] = commands

        synth = types.ModuleType("synthDriverHandler")
        synth.SynthDriver = _BaseDriver
        synth.VoiceInfo = lambda identifier, name: types.SimpleNamespace(id=identifier, name=name)
        synth.synthIndexReached = cls.indexAction
        synth.synthDoneSpeaking = cls.doneAction
        sys.modules["synthDriverHandler"] = synth

        nvwave = types.ModuleType("nvwave")
        nvwave.WavePlayer = _WavePlayer
        nvwave.AudioPurpose = types.SimpleNamespace(SPEECH="speech")
        sys.modules["nvwave"] = nvwave
        sys.modules["config"] = types.SimpleNamespace(conf={"audio": {"outputDevice": "default"}})
        sys.modules["logHandler"] = types.SimpleNamespace(log=types.SimpleNamespace(exception=lambda *args: None))
        driverSettings = types.ModuleType("autoSettingsUtils.driverSetting")
        driverSettings.BooleanDriverSetting = lambda *args, **kwargs: (args, kwargs)
        driverSettings.DriverSetting = lambda *args, **kwargs: (args, kwargs)
        driverSettings.NumericDriverSetting = lambda *args, **kwargs: (args, kwargs)
        settingsUtils = types.ModuleType("autoSettingsUtils.utils")
        settingsUtils.StringParameterInfo = lambda identifier, name: types.SimpleNamespace(
            id=identifier, name=name
        )
        autoSettings = types.ModuleType("autoSettingsUtils")
        autoSettings.driverSetting = driverSettings
        sys.modules["autoSettingsUtils"] = autoSettings
        sys.modules["autoSettingsUtils.driverSetting"] = driverSettings
        sys.modules["autoSettingsUtils.utils"] = settingsUtils

        synthPackage = types.ModuleType("synthDrivers")
        synthPackage.__path__ = [str(ROOT / "addon" / "synthDrivers")]
        sys.modules["synthDrivers"] = synthPackage
        spec = importlib.util.spec_from_file_location("synthDrivers.modernmono", ROOT / "addon" / "synthDrivers" / "modernmono.py")
        cls.driverModule = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.driverModule
        spec.loader.exec_module(cls.driverModule)

    def test_driver_speaks_and_reports_index_and_done(self):
        driver = self.driverModule.SynthDriver()
        try:
            driver.speak(["the thing", _IndexCommand(7), " is ready."])
            deadline = time.time() + 3
            while time.time() < deadline and not self.doneAction.calls:
                time.sleep(0.01)
            self.assertEqual(7, self.indexAction.calls[-1]["index"])
            self.assertTrue(self.doneAction.calls)
            self.assertTrue(driver._player.chunks)
        finally:
            driver.terminate()

    def test_driver_name_and_embedded_commands(self):
        driver = self.driverModule.SynthDriver()
        try:
            self.assertEqual("modernmono", driver.name)
            plain = driver._textToPhonetics("hello [[P8]] world <<~hEHlOW>>")
            self.assertNotIn("P8", plain)
            driver._embeddedCommands = True
            self.assertIn("P8", driver._textToPhonetics("hello `p8 world"))
            self.assertNotIn("P8", driver._textToPhonetics("<<P8 hello>>"))
            ordinary_ph = driver._textToPhonetics("ph hEHlOW")
            self.assertNotEqual("hEHlOW", ordinary_ph)
        finally:
            driver.terminate()

    def test_ascii_normalization_and_rate_boost(self):
        driver = self.driverModule.SynthDriver()
        try:
            self.assertEqual(
                set(driver._rateBoostModes), set(driver._get_availableRateboostmodes())
            )
            self.assertTrue({"clear", "maximum", "phaseAligned"}.isdisjoint(
                driver._rateBoostModes
            ))
            self.assertEqual("wasn't deja vu - ok...", driver._normalizeAscii("wasn’t déjà vu — ok…"))
            self.assertEqual((9, 0), driver._rateProfile(100))
            driver._rateBoostMode = "cleanReading"
            self.assertEqual((13, 0), driver._rateProfile(100))
            self.assertEqual(75, driver._pauseCompression(100))
            driver._rateBoostMode = "wsolaBalanced"
            self.assertEqual((13, 0), driver._rateProfile(100))
            self.assertEqual((2.0, 36, 0.5, 10), driver._wsolaProfile(100))
            self.assertEqual((1.0, 36, 0.5, 10), driver._wsolaProfile(40))
            self.assertEqual(50, driver._pauseCompression(100))
            original16 = bytes(range(256)) * 80
            wsola16 = driver._compressPcmWsola(original16, 1.7, 36, 0.5, 10)
            self.assertLess(len(wsola16), len(original16))
            original8 = bytes(range(256)) * 40
            wsola8 = driver._compressPcmWsola(original8, 1.7, 36, 0.5, 10, 1, 11025)
            self.assertLess(len(wsola8), len(original8))
            driver._rateBoostMode = "wholeUnits"
            self.assertEqual((24, 0), driver._rateProfile(100))
            driver._rateBoostMode = "legacyOverlap"
            self.assertEqual(13, driver._renderRate(100))
            original = bytes(range(256)) * 40
            self.assertLess(len(driver._compressPcm(original, 2.5)), len(original) * 0.55)
            compressed8 = driver._compressPcm(bytes(range(256)) * 8, 2.5, 1, 11025)
            self.assertLess(len(compressed8), 2048)
        finally:
            driver.terminate()

    def test_11khz_voice_uses_8bit_player(self):
        driver = self.driverModule.SynthDriver()
        try:
            driver._set_voice("11k8")
            self.assertEqual("11k8", driver._get_voice())
            player = driver._playerForRate(50)
            self.assertEqual(11025, player.options["samplesPerSec"])
            self.assertEqual(8, player.options["bitsPerSample"])
        finally:
            driver.terminate()

    def test_doubletalk_fast_variant_is_distinct_and_opt_in(self):
        driver = self.driverModule.SynthDriver()
        try:
            voices = driver._getAvailableVoices()
            self.assertIn("doubletalk22k16", voices)
            self.assertIn("doubletalk11k8", voices)
            ordinary = driver._textToPhonetics("unicode")
            driver._set_voice("doubletalk22k16")
            self.assertTrue(driver._isDoubleTalkVariant())
            self.assertNotEqual(ordinary, driver._textToPhonetics("unicode"))
            self.assertEqual((13, 48), driver._rateProfile(100))
            self.assertEqual(82, driver._pauseCompression(100))
            self.assertEqual((1.9, 24, 0.4, 7), driver._wsolaProfile(100))
            driver._set_voice("22k16")
            self.assertFalse(driver._isDoubleTalkVariant())
            self.assertEqual((9, 0), driver._rateProfile(100))
        finally:
            driver.terminate()

    def test_precise_pete_preset_is_bright_but_does_not_clip(self):
        driver = self.driverModule.SynthDriver()
        try:
            self.assertIn("precisePete22k16", driver._getAvailableVoices())
            driver._set_voice("precisePete22k16")
            self.assertTrue(driver._isPrecisePete())
            self.assertEqual(60, driver._get_pitch())
            self.assertEqual(5, driver._renderPitch(driver._get_pitch()))
            self.assertEqual(40, driver._renderExcitation())
            self.assertEqual("treble", driver._get_tone())
            self.assertEqual(8, round(driver._get_articulation() * 9 / 100))
            self.assertEqual(6, round(driver._get_formantFrequency() * 9 / 100))
            self.assertTrue(driver._useExpandedDictionary())
            samples = (-32000, -12000, 18000, 32000, -30000) * 20
            pcm = struct.pack(f"<{len(samples)}h", *samples)
            bright = driver._applyPrecisePeteTone(pcm, 2)
            values = struct.unpack(f"<{len(bright) // 2}h", bright)
            self.assertLessEqual(max(abs(value) for value in values), 32000)
            self.assertNotEqual(pcm, bright)
        finally:
            driver.terminate()

    def test_other_doubletalk_rom_presets_are_available_and_bounded(self):
        driver = self.driverModule.SynthDriver()
        try:
            voices = driver._getAvailableVoices()
            expected = {
                "doubleTalkVader22k16": (30, 7, 5, 1, 4, 2),
                "doubleTalkBigBob22k16": (40, 6, 1, 0, 4, 0),
                "doubleTalkRandy22k16": (40, 5, 2, 1, 5, 6),
            }
            samples = (-32000, -12000, 18000, 32000, -30000) * 500
            pcm = struct.pack(f"<{len(samples)}h", *samples)
            for voice, preset in expected.items():
                with self.subTest(voice=voice):
                    self.assertIn(voice, voices)
                    driver._set_voice(voice)
                    self.assertEqual(preset, driver._doubleTalkPreset())
                    coloured = driver._applyDoubleTalkPreset(pcm, 2, 22050)
                    values = struct.unpack(f"<{len(coloured) // 2}h", coloured)
                    self.assertLessEqual(max(abs(value) for value in values), 32000)
                    self.assertNotEqual(pcm, coloured)
        finally:
            driver.terminate()

    def test_doubletalk_voice_controls_override_preset_defaults(self):
        driver = self.driverModule.SynthDriver()
        try:
            driver._set_voice("doubleTalkBigBob22k16")
            self.assertEqual("bass", driver._get_tone())
            driver._set_formantFrequency(50)
            driver._set_articulation(100)
            driver._set_tone("treble")
            driver._set_reverb(30)
            self.assertEqual(50, driver._get_formantFrequency())
            self.assertEqual(100, driver._get_articulation())
            self.assertEqual("treble", driver._get_tone())
            self.assertEqual(30, driver._get_reverb())
            self.assertEqual(set(driver._tones), set(driver._get_availableTones()))
        finally:
            driver.terminate()


if __name__ == "__main__":
    unittest.main()
