import importlib.util
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
        driverSettings.NumericDriverSetting = lambda *args, **kwargs: (args, kwargs)
        autoSettings = types.ModuleType("autoSettingsUtils")
        autoSettings.driverSetting = driverSettings
        sys.modules["autoSettingsUtils"] = autoSettings
        sys.modules["autoSettingsUtils.driverSetting"] = driverSettings

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
            self.assertEqual("wasn't deja vu - ok...", driver._normalizeAscii("wasn’t déjà vu — ok…"))
            self.assertEqual(9, driver._monologRate(100))
            driver._rateBoost = True
            self.assertEqual(24, driver._monologRate(100))
            self.assertEqual(18, driver._monologRate(75))
            self.assertEqual(12, driver._monologRate(50))
            driver._legacyRateBoost = True
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


if __name__ == "__main__":
    unittest.main()
