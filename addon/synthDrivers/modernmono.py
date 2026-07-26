"""NVDA synth driver for the pure-Python Modern Mono engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
import re
import struct
from threading import Lock, Thread
from typing import Callable
import unicodedata

import config
from logHandler import log
import nvwave
from speech.commands import (
	BreakCommand,
	CharacterModeCommand,
	IndexCommand,
	PitchCommand,
	RateCommand,
	VolumeCommand,
)
import synthDriverHandler
from autoSettingsUtils.driverSetting import BooleanDriverSetting, DriverSetting, NumericDriverSetting
from autoSettingsUtils.utils import StringParameterInfo

from ._modernmono.engine import MonologEngine
from ._modernmono.phonetics import parse as parsePhonetics


_DIRECT_PHONETICS_RE = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)
_RAW_PHONETICS_RE = re.compile(r"<<~.*?>>", re.DOTALL)
_SETTING_COMMAND_RE = re.compile(r"<<[FMPSVfmpsv][0-9]?|>>", re.DOTALL)
_BACKTICK_COMMAND_RE = re.compile(r"`([psvd])\s*([0-9]+)", re.IGNORECASE)
_UNICODE_PUNCTUATION = str.maketrans({
	"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
	"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
	"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
	"\u2026": "...", "\u00a0": " ", "\u202f": " ",
})


@dataclass(frozen=True)
class _Job:
	sequence: tuple
	generation: int


class SynthDriver(synthDriverHandler.SynthDriver):
	# This must exactly match synthDrivers/modernmono.py. NVDA persists this
	# value and later imports synthDrivers.<name> when selecting the voice.
	name = "modernmono"
	description = "Modern Mono"
	supportedSettings = (
		synthDriverHandler.SynthDriver.VoiceSetting(),
		synthDriverHandler.SynthDriver.RateSetting(),
		DriverSetting(
			"rateBoostMode", "Rate &boost mode", defaultVal="off",
			availableInSettingsRing=True,
		),
		synthDriverHandler.SynthDriver.PitchSetting(),
		NumericDriverSetting(
			"excitation", "E&xcitation", defaultVal=50, availableInSettingsRing=True
		),
		NumericDriverSetting(
			"articulation", "&Articulation", defaultVal=50, availableInSettingsRing=True
		),
		NumericDriverSetting(
			"formantFrequency", "&Formant frequency", defaultVal=50,
			availableInSettingsRing=True,
		),
		DriverSetting(
			"tone", "T&one", defaultVal="normal", availableInSettingsRing=True,
		),
		NumericDriverSetting(
			"reverb", "Re&verb", defaultVal=0, availableInSettingsRing=True
		),
		synthDriverHandler.SynthDriver.VolumeSetting(),
		BooleanDriverSetting(
			"embeddedCommands",
			"Enable embedded &setting commands",
			defaultVal=False,
		),
		BooleanDriverSetting(
			"asciiNormalization",
			"Enable &ASCII normalization",
			defaultVal=True,
		),
		BooleanDriverSetting(
			"communityDictionary",
			"Use community &American English dictionary",
			defaultVal=False,
		),
	)
	supportedCommands = frozenset({
		IndexCommand,
		BreakCommand,
		CharacterModeCommand,
		PitchCommand,
		RateCommand,
		VolumeCommand,
	})
	supportedNotifications = frozenset({
		synthDriverHandler.synthIndexReached,
		synthDriverHandler.synthDoneSpeaking,
	})

	@classmethod
	def check(cls):
		root = Path(__file__).resolve().parents[1]
		return all((root / "data" / name).is_file() for name in (
			"FB_22K16.DLL", "FB_11K8.DLL", "FB_DEFLT.DIC"
		))

	def __init__(self):
		super().__init__()
		self._root = Path(__file__).resolve().parents[1]
		self._voice = "22k16"
		self._engine = MonologEngine(
			self._root / "data" / "FB_22K16.DLL",
			self._root / "data" / "FB_DEFLT.DIC",
			self._root / "data" / "IBMTTS_ENU.tsv",
			self._userDictionaryPath(self._root),
			self._root / "data" / "CMUdict.tsv",
		)
		self._player = self._newPlayer(22050, 16)
		self._players = {(22050, 16): self._player}
		self._activePlayer = self._player
		self._rate = 50
		self._rateBoostMode = "off"
		self._pitch = 50
		self._excitation = 50
		self._articulation = 50
		self._formantFrequency = 50
		self._tone = "normal"
		self._reverb = 0
		self._volume = 100
		self._embeddedCommands = False
		self._asciiNormalization = True
		self._communityDictionary = False
		self._jobs: Queue[_Job | None] = Queue()
		self._stateLock = Lock()
		self._generation = 0
		self._worker = Thread(target=self._run, name="ModernMonoSynth", daemon=True)
		self._worker.start()

	@staticmethod
	def _userDictionaryPath(root: Path) -> Path:
		getConfigPath = getattr(config, "getUserDefaultConfigPath", None)
		base = Path(getConfigPath()) if callable(getConfigPath) else root / "data"
		return base / "modernmono-user-dictionary.tsv"

	@staticmethod
	def _newPlayer(sampleRate: int, bitsPerSample: int):
		return nvwave.WavePlayer(
			channels=1,
			samplesPerSec=sampleRate,
			bitsPerSample=bitsPerSample,
			outputDevice=config.conf["audio"]["outputDevice"],
			wantDucking=True,
			purpose=nvwave.AudioPurpose.SPEECH,
		)

	def speak(self, speechSequence):
		with self._stateLock:
			generation = self._generation
		self._jobs.put(_Job(tuple(speechSequence), generation))

	def cancel(self):
		with self._stateLock:
			self._generation += 1
		while True:
			try:
				self._jobs.get_nowait()
			except Empty:
				break
		for player in self._players.values():
			player.stop()

	def pause(self, switch):
		for player in self._players.values():
			player.pause(bool(switch))

	def terminate(self):
		self.cancel()
		self._jobs.put(None)
		self._worker.join(timeout=2)
		for player in self._players.values():
			player.close()
		super().terminate()

	def _get_rate(self):
		return self._rate

	def _getAvailableVoices(self):
		return {
			"22k16": synthDriverHandler.VoiceInfo("22k16", "Monolog 22 kHz (16-bit)"),
			"11k8": synthDriverHandler.VoiceInfo("11k8", "Monolog 11 kHz (8-bit)"),
			"doubletalk22k16": synthDriverHandler.VoiceInfo(
				"doubletalk22k16", "Monolog DT Fast 22 kHz (expanded dictionary)"
			),
			"doubletalk11k8": synthDriverHandler.VoiceInfo(
				"doubletalk11k8", "Monolog DT Fast 11 kHz (expanded dictionary)"
			),
			"precisePete22k16": synthDriverHandler.VoiceInfo(
				"precisePete22k16", "Precise Pete (DoubleTalk preset, 22 kHz)"
			),
			"doubleTalkVader22k16": synthDriverHandler.VoiceInfo(
				"doubleTalkVader22k16", "Vader (DoubleTalk preset, 22 kHz)"
			),
			"doubleTalkBigBob22k16": synthDriverHandler.VoiceInfo(
				"doubleTalkBigBob22k16", "Big Bob (DoubleTalk preset, 22 kHz)"
			),
			"doubleTalkRandy22k16": synthDriverHandler.VoiceInfo(
				"doubleTalkRandy22k16", "Ricochet Randy (DoubleTalk preset, 22 kHz)"
			),
		}

	def _get_voice(self):
		return self._voice

	def _set_voice(self, value):
		value = str(value)
		# The DT Fast variants deliberately reuse the bundled Monolog acoustic
		# resources.  DoubleTalk's ROM is a research oracle, not distributable
		# voice data; the variants only select independently implemented timing
		# and dictionary policies inspired by the shared First Byte architecture.
		voices = {
			"22k16": "FB_22K16.DLL",
			"11k8": "FB_11K8.DLL",
			"doubletalk22k16": "FB_22K16.DLL",
			"doubletalk11k8": "FB_11K8.DLL",
			"precisePete22k16": "FB_22K16.DLL",
			"doubleTalkVader22k16": "FB_22K16.DLL",
			"doubleTalkBigBob22k16": "FB_22K16.DLL",
			"doubleTalkRandy22k16": "FB_22K16.DLL",
		}
		if value not in voices or value == self._voice:
			return
		self.cancel()
		self._engine = MonologEngine(
			self._root / "data" / voices[value],
			self._root / "data" / "FB_DEFLT.DIC",
			self._root / "data" / "IBMTTS_ENU.tsv",
			self._userDictionaryPath(self._root),
			self._root / "data" / "CMUdict.tsv",
		)
		self._voice = value
		self._applyVoiceDefaults()
		self._activePlayer = self._playerForRate(self._rate)

	def _set_rate(self, value):
		self._rate = max(0, min(100, int(value)))

	_rateBoostModes = {
		"off": StringParameterInfo("off", "Off (original Monolog)"),
		"cleanReading": StringParameterInfo(
			"cleanReading", "Clean reading (S13 + shorter pauses)"
		),
		"wsolaCrisp": StringParameterInfo(
			"wsolaCrisp", "WSOLA crisp (short windows)"
		),
		"wsolaBalanced": StringParameterInfo(
			"wsolaBalanced", "WSOLA balanced"
		),
		"wsolaSmooth": StringParameterInfo(
			"wsolaSmooth", "WSOLA smooth (long windows)"
		),
		"wsolaFast": StringParameterInfo(
			"wsolaFast", "WSOLA maximum speed"
		),
		"wsolaReading": StringParameterInfo(
			"wsolaReading", "WSOLA balanced + shorter pauses"
		),
		"wholeUnits": StringParameterInfo("wholeUnits", "Previous whole-unit boost"),
		"legacyOverlap": StringParameterInfo("legacyOverlap", "Legacy overlap/add boost"),
	}

	# NVDA forms this property with str.capitalize(), which lowercases every
	# character after the first: rateBoostMode -> availableRateboostmodes.
	def _get_availableRateboostmodes(self):
		return self._rateBoostModes

	def _get_rateBoostMode(self):
		return self._rateBoostMode

	def _set_rateBoostMode(self, value):
		if value in self._rateBoostModes:
			self._rateBoostMode = value

	def _get_pitch(self):
		return self._pitch

	def _set_pitch(self, value):
		self._pitch = max(0, min(100, int(value)))

	def _get_excitation(self):
		return self._excitation

	def _set_excitation(self, value):
		self._excitation = max(0, min(100, int(value)))

	def _get_articulation(self):
		return self._articulation

	def _set_articulation(self, value):
		self._articulation = max(0, min(100, int(value)))

	def _get_formantFrequency(self):
		return self._formantFrequency

	def _set_formantFrequency(self, value):
		self._formantFrequency = max(0, min(100, int(value)))

	_tones = {
		"bass": StringParameterInfo("bass", "Bass"),
		"normal": StringParameterInfo("normal", "Normal"),
		"treble": StringParameterInfo("treble", "Treble"),
	}

	def _get_availableTones(self):
		return self._tones

	def _get_tone(self):
		return self._tone

	def _set_tone(self, value):
		if value in self._tones:
			self._tone = value

	def _get_reverb(self):
		return self._reverb

	def _set_reverb(self, value):
		self._reverb = max(0, min(100, int(value)))

	def _get_volume(self):
		return self._volume

	def _set_volume(self, value):
		self._volume = max(0, min(100, int(value)))

	def _get_embeddedCommands(self):
		return self._embeddedCommands

	def _set_embeddedCommands(self, value):
		self._embeddedCommands = bool(value)

	def _get_asciiNormalization(self):
		return self._asciiNormalization

	def _set_asciiNormalization(self, value):
		self._asciiNormalization = bool(value)

	def _get_communityDictionary(self):
		return self._communityDictionary

	def _set_communityDictionary(self, value):
		self._communityDictionary = bool(value)

	def _isCurrent(self, generation: int) -> bool:
		with self._stateLock:
			return generation == self._generation

	def _rateProfile(self, value: int) -> tuple[int, int]:
		"""Return native scheduling speed and protected-unit compression percent."""
		value = max(0, min(100, value))
		if self._isDoubleTalkVariant():
			# The patent describes manipulating compact voice-period segments while
			# retaining their significant edges.  Drive the recovered scheduler to
			# its verified S13 limit, then shorten only the protected centre of each
			# unit.  The correlation splice used at render time keeps transitions
			# stable at high reading rates.
			nativeSpeed = round(value * 13 / 100)
			unitCompression = round(max(0, value - 35) * 48 / 65)
			return nativeSpeed, unitCompression
		if self._rateBoostMode == "off":
			return round(value * 9 / 100), 0
		if self._rateBoostMode in {
			"cleanReading", "wsolaCrisp", "wsolaBalanced", "wsolaSmooth",
			"wsolaFast", "wsolaReading",
		}:
			return round(value * 13 / 100), 0
		if self._rateBoostMode == "legacyOverlap":
			return round(value * 13 / 100), 0
		if value <= 75:
			return round(value * 18 / 75), 0
		return 18 + round((value - 75) * 6 / 25), 0

	def _renderRate(self, value: int) -> int:
		return self._rateProfile(value)[0]

	def _pauseCompression(self, value: int) -> int:
		if self._isDoubleTalkVariant():
			# Preserve some punctuation at ordinary rates, but make long pauses stop
			# dominating the cadence as the reading rate rises.
			return round(max(0, min(100, value)) * 82 / 100)
		if self._rateBoostMode in {"cleanReading", "wsolaReading"}:
			return round(max(0, min(100, value)) * 75 / 100)
		if self._rateBoostMode in {
			"wsolaCrisp", "wsolaBalanced", "wsolaSmooth", "wsolaFast",
		}:
			return round(max(0, min(100, value)) * 50 / 100)
		return 0

	def _wsolaProfile(self, value: int):
		"""Return factor, window ms, overlap fraction and search ms."""
		if self._isDoubleTalkVariant():
			# Unit scheduling supplies the first speed increase.  Add a conservative,
			# short-window utterance compressor above 50% for very fast reading.
			amount = max(0, min(50, value - 50)) / 50.0
			return 1.0 + 0.9 * amount, 24, 0.40, 7
		profiles = {
			"wsolaCrisp": (0.65, 24, 0.35, 6),
			"wsolaBalanced": (1.0, 36, 0.50, 10),
			"wsolaSmooth": (1.0, 52, 0.65, 14),
			"wsolaFast": (1.5, 32, 0.50, 12),
			"wsolaReading": (1.0, 40, 0.55, 12),
		}
		profile = profiles.get(self._rateBoostMode)
		if profile is None:
			return None
		maximumExtra, windowMs, overlap, searchMs = profile
		# Leave the lower 40% entirely native. Above it, progressively add the
		# whole-utterance compressor while the engine itself remains within S13.
		amount = max(0, min(60, value - 40)) / 60.0
		return 1.0 + maximumExtra * amount, windowMs, overlap, searchMs

	def _playerForRate(self, value: int):
		sampleRate = self._engine.manifest["sample_rate"]
		bitsPerSample = self._engine.manifest["bits_per_sample"]
		key = (sampleRate, bitsPerSample)
		player = self._players.get(key)
		if player is None:
			player = self._newPlayer(sampleRate, bitsPerSample)
			self._players[key] = player
		return player

	def _isDoubleTalkVariant(self) -> bool:
		return self._voice.startswith("doubletalk")

	def _isPrecisePete(self) -> bool:
		return self._voice == "precisePete22k16"

	_doubleTalkPresets = {
		# Exact ROM rows at 0x1A33: P, E, F, X, A, R.
		"doubleTalkVader22k16": (30, 7, 5, 1, 4, 2),
		"doubleTalkBigBob22k16": (40, 6, 1, 0, 4, 0),
		"precisePete22k16": (60, 4, 6, 2, 8, 0),
		"doubleTalkRandy22k16": (40, 5, 2, 1, 5, 6),
	}

	def _doubleTalkPreset(self):
		return self._doubleTalkPresets.get(self._voice)

	def _applyVoiceDefaults(self) -> None:
		preset = self._doubleTalkPreset()
		if preset is None:
			pitch, expression, formant, tone, articulation, reverb = (50, 5, 5, 1, 5, 0)
		else:
			pitch, expression, formant, tone, articulation, reverb = preset
		self._pitch = pitch
		self._excitation = expression * 10
		self._formantFrequency = round(formant * 100 / 9)
		self._tone = ("bass", "normal", "treble")[tone]
		self._articulation = round(articulation * 100 / 9)
		self._reverb = round(reverb * 100 / 9)

	def _useExpandedDictionary(self) -> bool:
		"""DT Fast favours coverage; ordinary voices retain the opt-in switch."""
		return (
			self._communityDictionary or self._isDoubleTalkVariant()
			or self._doubleTalkPreset() is not None
		)

	def _renderPitch(self, value: int) -> int:
		return self._monologSetting(value)

	def _renderExcitation(self) -> int:
		return self._excitation

	@staticmethod
	def _applyPrecisePeteTone(pcm: bytes, sampleWidth: int) -> bytes:
		"""Approximate Pete's F6/X2 bright response without importing ROM audio.

		A mild pre-emphasis models the treble preset.  Peak normalization makes
		the filter incapable of introducing the clipping heard in aggressive
		rate-compression experiments.
		"""
		if not pcm:
			return pcm
		if sampleWidth == 2:
			samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
			limit = 32767
		else:
			samples = [value - 0x80 for value in pcm]
			limit = 127
		originalPeak = max(1, max(abs(value) for value in samples))
		filtered = []
		previous = samples[0]
		for value in samples:
			filtered.append(value + (value - previous) * 3 // 16)
			previous = value
		filteredPeak = max(1, max(abs(value) for value in filtered))
		targetPeak = min(limit, originalPeak)
		if filteredPeak > targetPeak:
			filtered = [value * targetPeak // filteredPeak for value in filtered]
		if sampleWidth == 2:
			return struct.pack(f"<{len(filtered)}h", *filtered)
		return bytes(max(0, min(255, value + 0x80)) for value in filtered)

	@staticmethod
	def _applyDoubleTalkColour(
			pcm: bytes, sampleWidth: int, sampleRate: int,
			formant: int, tone: int, articulation: int, reverb: int,
	) -> bytes:
		"""Map DoubleTalk F/X/R presets to bounded time-domain treatments."""
		if not pcm:
			return pcm
		if sampleWidth == 2:
			samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
			limit = 32767
		else:
			samples = [value - 0x80 for value in pcm]
			limit = 127
		originalPeak = max(1, max(abs(value) for value in samples))

		# Lower F values darken the voice, but retain most of the dry signal so
		# consonants remain useful for fast screen-reader speech.  Earlier builds
		# cascaded low-pass stages here, which made Bob and Randy too muffled.
		if formant < 5:
			dry = samples
			previous = dry[0]
			smoothed = []
			for value in dry:
				previous = (previous + value) // 2
				smoothed.append(previous)
			darkness = min(4, 5 - formant)
			samples = [
				(value * (10 - darkness) + low * darkness) // 10
				for value, low in zip(dry, smoothed)
			]
		elif formant > 5 or tone == 2:
			previous = samples[0]
			amount = 3 if tone == 2 else 1
			filtered = []
			for value in samples:
				filtered.append(value + (value - previous) * amount // 16)
				previous = value
			samples = filtered

		# Bass tone adds only a small wet component; F1 already supplies Bob's
		# darker identity and should not erase stop/fricative transients.
		if tone == 0:
			previous = samples[0]
			for index, value in enumerate(tuple(samples)):
				previous = (previous + value) // 2
				samples[index] = (value * 7 + previous) // 8

		# Articulation is a restrained transient control.  High settings add a
		# little edge energy; low settings blend toward a one-sample smoother.
		if articulation != 5:
			dry = tuple(samples)
			previous = dry[0]
			if articulation > 5:
				amount = articulation - 5
				for index, value in enumerate(dry):
					samples[index] = value + (value - previous) * amount // 24
					previous = value
			else:
				amount = 5 - articulation
				for index, value in enumerate(dry):
					previous = (previous + value) // 2
					samples[index] = (value * (8 - amount) + previous * amount) // 8

		if reverb:
			delay = max(1, round(sampleRate * (18 + reverb * 4) / 1000))
			wet = min(5, 1 + reverb // 2)
			for index in range(delay, len(samples)):
				samples[index] += samples[index - delay] * wet // 16

		peak = max(1, max(abs(value) for value in samples))
		targetPeak = min(limit, originalPeak)
		if peak > targetPeak:
			samples = [value * targetPeak // peak for value in samples]
		if sampleWidth == 2:
			return struct.pack(f"<{len(samples)}h", *samples)
		return bytes(max(0, min(255, value + 0x80)) for value in samples)

	def _applyDoubleTalkPreset(self, pcm: bytes, sampleWidth: int, sampleRate: int) -> bytes:
		formant = round(self._formantFrequency * 9 / 100)
		tone = {"bass": 0, "normal": 1, "treble": 2}[self._tone]
		articulation = round(self._articulation * 9 / 100)
		reverb = round(self._reverb * 9 / 100)
		if (formant, tone, articulation, reverb) == (5, 1, 5, 0):
			return pcm
		return self._applyDoubleTalkColour(
			pcm, sampleWidth, sampleRate, formant, tone, articulation, reverb
		)

	@staticmethod
	def _monologSetting(value: int) -> int:
		return max(0, min(9, round(value * 9 / 100)))

	def _notifyIndex(self, generation: int, index: int) -> Callable[[], None]:
		def notify():
			if self._isCurrent(generation):
				synthDriverHandler.synthIndexReached.notify(synth=self, index=index)
		return notify

	def _notifyDone(self, generation: int) -> Callable[[], None]:
		def notify():
			if self._isCurrent(generation):
				synthDriverHandler.synthDoneSpeaking.notify(synth=self)
		return notify

	def _feed(self, text: str, *, characterMode: bool, rate: int, pitch: int,
			volume: int, generation: int, onDone=None) -> bool:
		if not self._isCurrent(generation):
			return False
		if characterMode:
			phonetics = self._engine.spell_to_phonetics(
				text, use_community_dictionary=self._useExpandedDictionary()
			)
		else:
			phonetics = self._textToPhonetics(text)
		if not phonetics:
			if onDone:
				onDone()
			return True
		# V5 is the original nominal level. Lower values attenuate in the
		# renderer; NVDA's stream volume supplies the full 0-100 range.
		renderRate, unitCompression = self._rateProfile(rate)
		audio = self._engine.render_phonetics(
			f"V{min(5, self._monologSetting(volume))}{phonetics}",
			pitch=self._renderPitch(pitch),
			speed=renderRate,
			excitation=self._renderExcitation(),
			unit_compression=unitCompression,
			compression_method=(
				"correlation" if self._isDoubleTalkVariant() else "centre"
			),
			pause_compression=self._pauseCompression(rate),
		)
		pcm = audio.pcm
		wsola = self._wsolaProfile(rate)
		if wsola is not None:
			pcm = self._compressPcmWsola(
				pcm, *wsola, audio.sample_width, audio.sample_rate
			)
		if self._rateBoostMode == "legacyOverlap" and rate > 0:
			pcm = self._compressPcm(
				pcm, 1.0 + 1.5 * rate / 100.0, audio.sample_width, audio.sample_rate
			)
		pcm = self._applyDoubleTalkPreset(pcm, audio.sample_width, audio.sample_rate)
		if not self._isCurrent(generation):
			return False
		player = self._playerForRate(rate)
		self._activePlayer = player
		player.setVolume(all=volume / 100.0)
		player.feed(pcm, onDone=onDone)
		return True

	@staticmethod
	def _compressPcm(pcm: bytes, factor: float, sampleWidth: int = 2,
			sampleRate: int = 22050) -> bytes:
		"""Yesterday's pitch-preserving 20 ms overlap/add rate boost."""
		window = 440 if sampleRate == 22050 else max(2, sampleRate // 50 & ~1)
		if factor <= 1.0 or len(pcm) < window * sampleWidth * 2:
			return pcm
		if sampleWidth == 2:
			samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
		else:
			samples = [value - 0x80 for value in pcm]
		overlap = synthesisHop = window // 2
		analysisHop = max(synthesisHop + 1, round(synthesisHop * factor))
		result = samples[:window]
		source = analysisHop
		destination = synthesisHop
		while source + window <= len(samples):
			frame = samples[source:source + window]
			needed = destination + window
			if len(result) < needed:
				result.extend([0] * (needed - len(result)))
			for index in range(overlap):
				left = overlap - index
				result[destination + index] = (
					result[destination + index] * left + frame[index] * index
				) // overlap
			result[destination + overlap:destination + window] = frame[overlap:]
			source += analysisHop
			destination += synthesisHop
		if sampleWidth == 2:
			return struct.pack(f"<{len(result)}h", *result)
		return bytes(max(0, min(255, value + 0x80)) for value in result)

	@staticmethod
	def _compressPcmWsola(pcm: bytes, factor: float, windowMs: int,
			overlapFraction: float, searchMs: int, sampleWidth: int = 2,
			sampleRate: int = 22050) -> bytes:
		"""Correlation-aligned whole-utterance overlap/add compression."""
		if factor <= 1.0:
			return pcm
		if sampleWidth == 2:
			samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
		else:
			samples = [value - 0x80 for value in pcm]
		window = max(16, round(sampleRate * windowMs / 1000))
		overlap = max(4, min(window - 4, round(window * overlapFraction)))
		synthesisHop = window - overlap
		analysisHop = max(synthesisHop + 1, round(synthesisHop * factor))
		search = max(2, round(sampleRate * searchMs / 1000))
		if len(samples) < window * 2:
			return pcm

		result = samples[:window]
		source = 0
		destination = synthesisHop
		while True:
			expected = source + analysisHop
			low = max(source + 1, expected - search)
			high = min(len(samples) - window, expected + search)
			if low > high:
				break
			bestSource = low
			bestError = None
			# Sampling every fourth point is sufficient to locate waveform phase
			# and keeps synthesis responsive inside NVDA.
			for candidate in range(low, high + 1, 4):
				error = 0
				for index in range(0, overlap, 8):
					difference = result[destination + index] - samples[candidate + index]
					error += difference * difference
				if bestError is None or error < bestError:
					bestError = error
					bestSource = candidate
			frame = samples[bestSource:bestSource + window]
			for index in range(overlap):
				left = overlap - index
				result[destination + index] = (
					result[destination + index] * left + frame[index] * index
				) // overlap
			result.extend(frame[overlap:])
			source = bestSource
			destination += synthesisHop
		if sampleWidth == 2:
			return struct.pack(f"<{len(result)}h", *result)
		return bytes(max(0, min(255, value + 0x80)) for value in result)

	def previewPhonetics(self, phonetics: str) -> None:
		parsePhonetics(phonetics)
		renderRate, unitCompression = self._rateProfile(self._rate)
		audio = self._engine.render_phonetics(
			phonetics,
			pitch=self._renderPitch(self._pitch),
			speed=renderRate,
			excitation=self._renderExcitation(),
			unit_compression=unitCompression,
			compression_method=(
				"correlation" if self._isDoubleTalkVariant() else "centre"
			),
			pause_compression=self._pauseCompression(self._rate),
		)
		pcm = audio.pcm
		wsola = self._wsolaProfile(self._rate)
		if wsola is not None:
			pcm = self._compressPcmWsola(
				pcm, *wsola, audio.sample_width, audio.sample_rate
			)
		if self._rateBoostMode == "legacyOverlap" and self._rate > 0:
			pcm = self._compressPcm(
				pcm, 1.0 + 1.5 * self._rate / 100.0,
				audio.sample_width, audio.sample_rate,
			)
		pcm = self._applyDoubleTalkPreset(pcm, audio.sample_width, audio.sample_rate)
		player = self._playerForRate(self._rate)
		player.stop()
		player.setVolume(all=self._volume / 100.0)
		player.feed(pcm)

	def getUserDictionaryEntries(self) -> dict[str, str]:
		return dict(self._engine.user_phonetics)

	def setUserDictionaryEntry(self, spelling: str, phonetics: str | None) -> None:
		self._engine.set_user_phonetics(spelling, phonetics)

	def _textToPhonetics(self, text: str) -> str:
		"""Apply optional backtick setting commands."""
		if self._asciiNormalization:
			text = self._normalizeAscii(text)
		# Remove retired public forms before creating private parsing markers.
		text = _DIRECT_PHONETICS_RE.sub(" ", text)
		text = _RAW_PHONETICS_RE.sub(" ", text)
		text = _SETTING_COMMAND_RE.sub(" ", text)
		if not self._embeddedCommands:
			text = _BACKTICK_COMMAND_RE.sub(" ", text)
		else:
			text = _BACKTICK_COMMAND_RE.sub(
				lambda match: f"[[{match.group(1).upper()}{match.group(2)}]]", text
			)
		if "[[" not in text:
			return self._engine.text_to_phonetics(
				text, use_community_dictionary=self._useExpandedDictionary()
			)

		pieces = []
		offset = 0
		for match in _DIRECT_PHONETICS_RE.finditer(text):
			ordinary = text[offset:match.start()]
			if ordinary.strip():
				pieces.append(self._engine.text_to_phonetics(
					ordinary, use_community_dictionary=self._useExpandedDictionary()
				))
			raw = match.group(1).strip()
			if raw:
				parsePhonetics(raw)
				pieces.append(raw)
			offset = match.end()
		ordinary = text[offset:]
		if ordinary.strip():
			pieces.append(self._engine.text_to_phonetics(
				ordinary, use_community_dictionary=self._useExpandedDictionary()
			))
		return "|".join(pieces)

	@staticmethod
	def _normalizeAscii(text: str) -> str:
		# Preserve the one non-ASCII currency sign understood by the front end.
		placeholder = "\x00POUND\x00"
		text = text.translate(_UNICODE_PUNCTUATION).replace("£", placeholder)
		text = unicodedata.normalize("NFKD", text)
		text = "".join(char for char in text if not unicodedata.combining(char))
		text = text.encode("ascii", "ignore").decode("ascii")
		return text.replace(placeholder, "£")

	def _run(self):
		while True:
			job = self._jobs.get()
			if job is None:
				return
			try:
				self._renderJob(job)
			except Exception:
				log.exception("Modern Mono failed to synthesize speech")
				if self._isCurrent(job.generation):
					synthDriverHandler.synthDoneSpeaking.notify(synth=self)

	def _renderJob(self, job: _Job):
		if not self._isCurrent(job.generation):
			return
		rate, pitch, volume = self._rate, self._pitch, self._volume
		characterMode = False
		text: list[str] = []
		pendingCallbacks: list[Callable[[], None]] = []

		def combinedCallback(extra=None):
			callbacks = [*pendingCallbacks]
			pendingCallbacks.clear()
			if extra:
				callbacks.append(extra)
			if not callbacks:
				return None
			def runCallbacks():
				for callback in callbacks:
					callback()
			return runCallbacks

		def flush(extra=None):
			if text:
				value = "".join(text)
				text.clear()
				return self._feed(value, characterMode=characterMode, rate=rate, pitch=pitch,
					volume=volume, generation=job.generation, onDone=combinedCallback(extra))
			callback = combinedCallback(extra)
			if callback:
				callback()
			return self._isCurrent(job.generation)

		for item in job.sequence:
			if not self._isCurrent(job.generation):
				return
			if isinstance(item, str):
				text.append(item)
			elif isinstance(item, IndexCommand):
				if text:
					if not flush(self._notifyIndex(job.generation, item.index)):
						return
				else:
					pendingCallbacks.append(self._notifyIndex(job.generation, item.index))
			elif isinstance(item, CharacterModeCommand):
				if not flush(): return
				characterMode = item.state
			elif isinstance(item, BreakCommand):
				if not flush(): return
				# Monolog delay units are approximately 100 ms.
				units = max(1, round(item.time / 100)) if item.time else 1
				audio = self._engine.render_phonetics(f"D{min(9, units)}")
				self._activePlayer.feed(audio.pcm)
			elif isinstance(item, RateCommand):
				if not flush(): return
				rate = max(0, min(100, item.newValue))
			elif isinstance(item, PitchCommand):
				if not flush(): return
				pitch = max(0, min(100, item.newValue))
			elif isinstance(item, VolumeCommand):
				if not flush(): return
				volume = max(0, min(100, item.newValue))
		if not flush(self._notifyDone(job.generation)):
			return
		for player in self._players.values():
			player.idle()
