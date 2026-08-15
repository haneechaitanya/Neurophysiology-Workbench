from __future__ import annotations

import numpy as np
import mne

from erpworkbench import engine


def main():
    rng = np.random.default_rng(23)
    sfreq = 200.0
    n_times = 4000
    ch_names = ["Fp1", "Fp2", "Cz", "Pz"]
    info = mne.create_info(ch_names, sfreq, ch_types="eeg")
    data = rng.normal(scale=5e-6, size=(len(ch_names), n_times))
    # Add a stereotyped frontal transient so the decomposition is non-trivial.
    for center in (600, 1600, 2600, 3400):
        x = np.arange(n_times) - center
        blink = 80e-6 * np.exp(-(x / 20.0) ** 2)
        data[0] += blink
        data[1] += 0.8 * blink
    raw = mne.io.RawArray(data, info, verbose="ERROR")
    before = raw.get_data().copy()

    ica = engine.run_ica(raw, method="fastica", n_components=4, random_state=7)

    expected = raw.copy().load_data()
    ica.apply(expected, exclude=[0], verbose="ERROR")

    messages: list[str] = []
    actual = engine.apply_ica(
        raw,
        ica,
        [0],
        progress=messages.append,
        chunk_duration_sec=2.0,
    )

    np.testing.assert_allclose(actual.get_data(), expected.get_data(), rtol=1e-10, atol=1e-14)
    np.testing.assert_allclose(raw.get_data(), before, rtol=0, atol=0)
    assert any(msg.startswith("ICA_RECON_PROGRESS|100|") for msg in messages), messages[-3:]
    assert actual.n_times == raw.n_times
    assert actual.ch_names == raw.ch_names
    print("ICA_CHUNKED_RECONSTRUCTION_V10_SMOKE_TEST_OK", len(messages), actual.n_times)


if __name__ == "__main__":
    main()
