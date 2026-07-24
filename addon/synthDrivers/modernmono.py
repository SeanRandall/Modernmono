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
from autoSettingsUtils.driverSetting import BooleanDriverSetting, NumericDriverSetting

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
		synthDriverHandler.SynthDriver.RateBoostSetting(),
		synthDriverHandler.SynthDriver.PitchSetting(),
		NumericDriverSetting(
			"excitation", "E&xcitation", defaultVal=50, availableInSettingsRing=True
		),
		synthDriverHandler.SynthDriver.VolumeSetting(),
		BooleanDriverSetting(
			"legacyRateBoost",
			"Use &legacy overlap rate boost",
			defaultVal=False,
		),
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
		self._rateBoost = False
		self._legacyRateBoost = False
		self._pitch = 50
		self._excitation = 50
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
		}

	def _get_voice(self):
		return self._voice

	def _set_voice(self, value):
		value = str(value)
		voices = {"22k16": "FB_22K16.DLL", "11k8": "FB_11K8.DLL"}
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
		self._activePlayer = self._playerForRate(self._rate)

	def _set_rate(self, value):
		self._rate = max(0, min(100, int(value)))

	def _get_rateBoost(self):
		return self._rateBoost

	def _set_rateBoost(self, value):
		self._rateBoost = bool(value)

	def _get_legacyRateBoost(self):
		return self._legacyRateBoost

	def _set_legacyRateBoost(self, value):
		self._legacyRateBoost = bool(value)

	def _get_pitch(self):
		return self._pitch

	def _set_pitch(self, value):
		self._pitch = max(0, min(100, int(value)))

	def _get_excitation(self):
		return self._excitation

	def _set_excitation(self, value):
		self._excitation = max(0, min(100, int(value)))

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

	def _monologRate(self, value: int) -> int:
		value = max(0, min(100, value))
		if not self._rateBoost:
			return round(value * 9 / 100)
		# Keep whole-unit dropping out of the commonly used first 75%. The
		# final quarter extends S18 to S24 at a deliberately gentler slope.
		if value <= 75:
			return round(value * 18 / 75)
		return 18 + round((value - 75) * 6 / 25)

	def _renderRate(self, value: int) -> int:
		if self._rateBoost and self._legacyRateBoost:
			return max(0, min(13, round(value * 13 / 100)))
		return self._monologRate(value)

	def _playerForRate(self, value: int):
		sampleRate = self._engine.manifest["sample_rate"]
		bitsPerSample = self._engine.manifest["bits_per_sample"]
		key = (sampleRate, bitsPerSample)
		player = self._players.get(key)
		if player is None:
			player = self._newPlayer(sampleRate, bitsPerSample)
			self._players[key] = player
		return player

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
				text, use_community_dictionary=self._communityDictionary
			)
		else:
			phonetics = self._textToPhonetics(text)
		if not phonetics:
			if onDone:
				onDone()
			return True
		# V5 is the original nominal level. Lower values attenuate in the
		# renderer; NVDA's stream volume supplies the full 0-100 range.
		audio = self._engine.render_phonetics(
			f"V{min(5, self._monologSetting(volume))}{phonetics}",
			pitch=self._monologSetting(pitch),
			speed=self._renderRate(rate),
			excitation=self._excitation,
		)
		pcm = audio.pcm
		if self._rateBoost and self._legacyRateBoost and rate > 0:
			pcm = self._compressPcm(
				pcm, 1.0 + 1.5 * rate / 100.0, audio.sample_width, audio.sample_rate
			)
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

	def previewPhonetics(self, phonetics: str) -> None:
		parsePhonetics(phonetics)
		audio = self._engine.render_phonetics(
			phonetics,
			pitch=self._monologSetting(self._pitch),
			speed=self._renderRate(self._rate),
			excitation=self._excitation,
		)
		pcm = audio.pcm
		if self._rateBoost and self._legacyRateBoost and self._rate > 0:
			pcm = self._compressPcm(
				pcm, 1.0 + 1.5 * self._rate / 100.0,
				audio.sample_width, audio.sample_rate,
			)
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
				text, use_community_dictionary=self._communityDictionary
			)

		pieces = []
		offset = 0
		for match in _DIRECT_PHONETICS_RE.finditer(text):
			ordinary = text[offset:match.start()]
			if ordinary.strip():
				pieces.append(self._engine.text_to_phonetics(
					ordinary, use_community_dictionary=self._communityDictionary
				))
			raw = match.group(1).strip()
			if raw:
				parsePhonetics(raw)
				pieces.append(raw)
			offset = match.end()
		ordinary = text[offset:]
		if ordinary.strip():
			pieces.append(self._engine.text_to_phonetics(
				ordinary, use_community_dictionary=self._communityDictionary
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
