"""Regenerate translations/en.json (device trigger labels come from const.py).

.venv/bin/python scripts/gen_translations.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))

from remote_bridge.const import TRIGGER_TYPES  # noqa: E402

CONTROL_LABELS = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "select": "Select",
    "back": "Back",
    "home": "TV button",
    "input": "Input (TV button held)",
    "home_double": "TV button double-press",
    "play_pause": "Play/Pause",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "mute": "Mute",
    "power": "Power",
    "siri": "Siri",
    "skip_forward": "Skip forward",
    "skip_back": "Skip back",
    "keyboard_text": "Keyboard text",
}
PRESS_LABELS = {
    "short": "pressed",
    "long": "held",
    "double": "pressed",
    "repeat": "repeating while held",
    "text": "entered",
}

translations = {
    "config": {
        "step": {
            "user": {
                "title": "Add a virtual TV",
                "description": (
                    "Creates a TV that your iPhone or iPad can control from "
                    "Control Center → Remote. Make one per room."
                ),
                "data": {"name": "Name", "port": "Port"},
                "data_description": {
                    "name": "Shown in the phone's remote device list.",
                    "port": "TCP port for the phone to connect to. 0 picks a free port.",
                },
            }
        },
        "error": {
            "name_required": "Enter a name.",
            "name_in_use": "A virtual TV with this name already exists.",
        },
    },
    "options": {
        "step": {
            "init": {
                "title": "Remote Bridge options",
                "data": {"allow_pairing": "Allow new pairings"},
                "data_description": {
                    "allow_pairing": (
                        "Lets a new phone pair. Switches itself off after a successful pairing."
                    )
                },
            }
        }
    },
    "entity": {
        "binary_sensor": {"connected": {"name": "Connected"}},
        "sensor": {"last_button": {"name": "Last button"}},
        "button": {"forget_paired_devices": {"name": "Forget paired devices"}},
    },
    "device_automation": {
        "trigger_type": {
            key: f"{CONTROL_LABELS[control]} {PRESS_LABELS[press]}"
            for key, (control, press) in TRIGGER_TYPES.items()
        }
    },
}

out = ROOT / "custom_components/remote_bridge/translations/en.json"
out.write_text(json.dumps(translations, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {out.relative_to(ROOT)} ({len(TRIGGER_TYPES)} trigger types)")
