import numpy as np
import mne

from erpworkbench import engine

rng = np.random.default_rng(42)
sfreq = 100.0
duration = 30.0
n = int(sfreq * duration)
t = np.arange(n) / sfreq

blink = np.zeros(n)
for center in (4.0, 8.5, 13.0, 18.0, 24.0, 27.0):
    blink += np.exp(-0.5 * ((t - center) / 0.10) ** 2) * 8e-5

brain1 = 8e-6 * np.sin(2 * np.pi * 8 * t)
brain2 = 5e-6 * np.sin(2 * np.pi * 12 * t + 0.7)
noise = rng.normal(scale=2e-6, size=(4, n))

data = np.vstack([
    1.00 * blink + brain1,
    0.85 * blink - 0.4 * brain1 + brain2,
    0.10 * blink + brain1 + 0.5 * brain2,
    0.05 * blink - 0.3 * brain1 + brain2,
]) + noise

info = mne.create_info(["Fp1", "Fp2", "Cz", "Pz"], sfreq, ch_types="eeg")
raw = mne.io.RawArray(data, info, verbose="ERROR")
raw.set_montage(mne.channels.make_standard_montage("standard_1020"), on_missing="ignore", verbose="ERROR")
raw.set_eeg_reference("average", projection=False, verbose="ERROR")

ica = engine.run_ica(raw, method="fastica", n_components=4, random_state=97)
result = engine.blink_component_correlations(
    raw,
    ica,
    fit_exclude_spans=[{"start_sec": 21.0, "end_sec": 21.5, "reason": "movement"}],
)

scores = np.asarray(result["scores"], dtype=float)
assert scores.shape == (ica.n_components_,)
assert result["reference_channels"][:2] == ["Fp1", "Fp2"]
assert np.isfinite(scores).any()
assert float(np.nanmax(scores)) > 0.30, scores
assert ica.exclude == [], "Blink aid must never auto-remove components"
assert not any(str(x).startswith("BAD_ICA_EXCLUDE") for x in raw.annotations.description), "Input Raw was modified"

print("ICA_BLINK_AID_V10_SMOKE_TEST_OK", round(float(np.nanmax(scores)), 3), result["reference_channel"][int(np.nanargmax(scores))])
