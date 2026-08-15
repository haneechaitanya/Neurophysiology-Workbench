from __future__ import annotations

from erpworkbench.models import ComponentDefinition, ProtocolDefinition


def main():
    protocol = ProtocolDefinition(
        name="My ERP",
        display_channels=["Fz", "Cz", "Pz"],
        components=[
            ComponentDefinition("P3", 300, 500, "positive", "peak", ["Pz"]),
            ComponentDefinition("N450", 350, 550, "negative", "mean", ["Fz", "Cz"]),
        ],
    )
    restored = ProtocolDefinition.from_dict(protocol.to_dict())
    assert restored.display_channels == ["Fz", "Cz", "Pz"]
    assert [(c.name, c.method, c.channels) for c in restored.components] == [
        ("P3", "peak", ["Pz"]),
        ("N450", "mean", ["Fz", "Cz"]),
    ]
    print("PROTOCOL_DISPLAY_COMPONENTS_V10_SMOKE_TEST_OK")


if __name__ == "__main__":
    main()
