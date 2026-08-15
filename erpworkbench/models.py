from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class FilterSettings:
    enabled: bool = False
    l_freq: Optional[float] = 0.5
    h_freq: Optional[float] = 35.0
    notch_enabled: bool = False
    notch_freq: float = 50.0


@dataclass
class InterpolationSettings:
    enabled: bool = False
    bad_channels: list[str] = field(default_factory=list)
    montage: str = "standard_1020"


@dataclass
class ReferenceSettings:
    enabled: bool = False
    mode: str = "average"  # average | custom
    custom_channels: list[str] = field(default_factory=list)


@dataclass
class ICASettings:
    enabled: bool = False
    method: str = "fastica"
    n_components: Optional[float | int] = 0.99
    random_state: int = 97
    excluded_components: list[int] = field(default_factory=list)
    # Optional MNE-supported sample decimation used only while fitting ICA.
    # None means use every sample. The fitted solution is still applied to the
    # full-resolution processed recording.
    fit_decim: Optional[int] = None
    # User-marked continuous-data spans excluded only while estimating ICA.
    # These do NOT become BAD annotations on the analysis Raw and therefore do
    # not silently alter later epoch rejection. Each entry stores start_sec,
    # end_sec and an optional reason.
    fit_exclude_spans: list[dict] = field(default_factory=list)
    # Which continuous signal was deliberately used for downstream epoching.
    # ``pre_ica`` keeps the processed EEG before ICA reconstruction;
    # ``ica_cleaned`` uses the reconstruction produced after selected components
    # were removed. The GUI preserves both versions so this choice is reversible.
    epoch_input: str = "pre_ica"


@dataclass
class PreprocessingSettings:
    filter: FilterSettings = field(default_factory=FilterSettings)
    interpolation: InterpolationSettings = field(default_factory=InterpolationSettings)
    reference: ReferenceSettings = field(default_factory=ReferenceSettings)
    ica: ICASettings = field(default_factory=ICASettings)
    # User-visible order for structural preprocessing steps. Filtering is
    # intentionally excluded because it is treated as order-independent in the
    # GUI. Typical order: ["interpolation", "reference", "ica"].
    step_order: list[str] = field(default_factory=list)


@dataclass
class EpochSettings:
    tmin_ms: float = -200.0
    tmax_ms: float = 1000.0
    baseline_enabled: bool = True
    baseline_start_ms: Optional[float] = -200.0
    baseline_end_ms: Optional[float] = 0.0
    event_source: str = "annotations"  # annotations | stim
    stim_channel: str = ""
    absolute_threshold_uv: Optional[float] = 75.0
    p2p_threshold_uv: Optional[float] = None
    flat_threshold_uv: Optional[float] = None
    # Empty means all EEG channels. When populated, automatic epoch screening
    # uses only these channels; the epochs themselves still retain all channels.
    rejection_channels: list[str] = field(default_factory=list)


@dataclass
class ComponentDefinition:
    name: str
    start_ms: float
    end_ms: float
    polarity: str = "negative"  # positive | negative | absolute
    method: str = "peak"  # peak | mean | manual | area
    channels: list[str] = field(default_factory=list)


@dataclass
class EventGroupRule:
    pattern: str
    condition: str
    case_sensitive: bool = False
    starts_with: bool = False
    enabled: bool = True


@dataclass
class ProtocolDefinition:
    name: str = "New protocol"
    epoch: EpochSettings = field(default_factory=EpochSettings)
    # Raw event id -> condition label. Keys are stored as strings for JSON friendliness.
    event_map: dict[str, str] = field(default_factory=dict)
    # Reusable literal substring rules for annotation descriptions. These are
    # especially useful when every stimulus filename is unique but shares a
    # condition string, e.g. Neu_Red_001.jpg, Neu_Red_002.jpg -> Neu Red.
    event_groups: list[EventGroupRule] = field(default_factory=list)
    # Optional per-condition stimulus exclusions. Keys are resolved condition names
    # and values are exact annotation/event labels omitted before epoch preview/cut.
    # This allows a user to keep a broad string grouping while excluding one or
    # more specific stimuli without changing the raw recording or annotations.
    excluded_event_labels: dict[str, list[str]] = field(default_factory=dict)
    # Channels selected by default when this protocol is loaded. This is a
    # display/workflow preference only; it never removes channels from the EEG.
    display_channels: list[str] = field(default_factory=list)
    components: list[ComponentDefinition] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProtocolDefinition":
        epoch = EpochSettings(**data.get("epoch", {}))
        groups = [EventGroupRule(**g) for g in data.get("event_groups", [])]
        components = [ComponentDefinition(**c) for c in data.get("components", [])]
        return cls(
            name=data.get("name", "Imported protocol"),
            epoch=epoch,
            event_map={str(k): str(v) for k, v in data.get("event_map", {}).items()},
            event_groups=groups,
            excluded_event_labels={
                str(k): [str(x) for x in (v or [])]
                for k, v in data.get("excluded_event_labels", {}).items()
            },
            display_channels=[str(x) for x in data.get("display_channels", [])],
            components=components,
        )


@dataclass
class MeasurementResult:
    condition: str
    channel: str
    component: str
    method: str
    window_start_ms: float
    window_end_ms: float
    amplitude_uv: float
    latency_ms: Optional[float]
    n_epochs: int
    notes: str = ""


@dataclass
class EpochReviewState:
    auto_bad: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    auto_reason: list[str] = field(default_factory=list)
    # 0 = no manual decision, 1 = force keep, -1 = force reject
    manual_decision: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))

    def accepted_mask(self) -> np.ndarray:
        if self.auto_bad.size == 0:
            return np.zeros(0, dtype=bool)
        accepted = ~self.auto_bad.copy()
        accepted[self.manual_decision == 1] = True
        accepted[self.manual_decision == -1] = False
        return accepted


@dataclass
class SessionMetadata:
    input_path: Optional[Path] = None
    subject_id: str = ""
    notes: str = ""
    annotation_path: Optional[Path] = None
    annotation_count: int = 0
    annotation_out_of_range: int = 0
    annotation_duplicates_skipped: int = 0
    processing_log: list[str] = field(default_factory=list)
