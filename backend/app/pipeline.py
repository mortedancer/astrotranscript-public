"""Pipeline config used for the current batch (and future reprocess)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EnhanceEngine = Literal["afftdn", "deepfilternet3", "rnnoise", "spectral", "webrtc", "none"]
VadEngine = Literal["silero", "hybrid", "energy", "whisper_semantic"]


@dataclass
class EnhanceConfig:
    enabled: bool = True
    engine: EnhanceEngine = "afftdn"
    highpass_hz: int = 90
    lowpass_hz: int = 7500
    nr: int = 8
    nf: int = -28
    agc: bool = True
    limiter: bool = True
    strength: float = 0.55


@dataclass
class VADConfig:
    enabled: bool = True
    engine: VadEngine = "silero"
    adaptive: bool = True
    threshold: float = 0.42
    min_speech_ms: int = 350
    min_silence_ms: int = 600
    speech_pad_ms: int = 250
    max_speech_s: float = 28.0
    merge_gap_s: float = 0.70
    min_keep_s: float = 0.50
    window_s: int = 300


@dataclass
class AsrConfig:
    engine: str = "faster-whisper"
    model: str = "large-v3"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "ru"
    beam_size: int = 5
    vad_filter: bool = True


@dataclass
class PipelineConfig:
    enhance: EnhanceConfig = field(default_factory=EnhanceConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_PIPELINE = PipelineConfig()
