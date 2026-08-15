from pathlib import Path
import tempfile

import mne
import numpy as np

from erpworkbench import engine


def main():
    with tempfile.TemporaryDirectory() as fixture_dir:
        source = Path(fixture_dir) / "Synthetic_Annotation.txt"
        source.write_text(
            "number\tName\tlatency\turevent\tduration\tchannel\n"
            "1\tFixation.PNG\t1.0\t1\t-1\t-1\n"
            "2\tNeu_Red_001.PNG\t3.0\t2\t-1\t-1\n"
            "3\tNeu_Green_001.PNG\t4.0\t3\t-1\t-1\n"
            "4\tInstruction.PNG\t5.0\t4\t-1\t-1\n",
            encoding="utf-8",
        )
        table = engine.read_annotation_txt(source)
        assert len(table) == 4
        assert float(table.iloc[3]['latency']) == 5.0
        assert str(table.iloc[3]['description']) == 'Instruction.PNG'

        info = mne.create_info(['Cz'], 10.0, ['eeg'])
        raw = mne.io.RawArray(np.zeros((1, 1000)), info, verbose=False)
        base = raw.annotations.copy()
        stats = engine.attach_external_annotations(raw, base, table)
        assert stats['attached'] == 4
        assert len(raw.annotations) == 4
        events, labels = engine.discover_events(raw, 'annotations', '')
        assert len(events) == 4
        assert any(v == 'Instruction.PNG' for v in labels.values())

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / 'Subject01.edf').touch()
        ann = d / 'Subject01_Annotation.txt'
        ann.write_text('number\tName\tlatency\turevent\tduration\tchannel\n1\tStimulus.PNG\t1.0\t1\t-1\t-1\n', encoding='utf-8')
        assert engine.find_companion_annotation_file(d / 'Subject01.edf') == ann

    print('ANNOTATION_SMOKE_TEST_OK')


if __name__ == '__main__':
    main()
