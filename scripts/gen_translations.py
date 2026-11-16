"""Regenerate translations/en.json (device trigger labels come from const.py).

.venv/bin/python scripts/gen_translations.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))

from remote_bridge.const import (  # noqa: E402
    MAPPING_GROUPS,
    MAX_INPUT_SOURCES,
    REPEAT_CAPABLE,
    TRIGGER_TYPES,
)

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


def mapping_label(key: str) -> str:
    control, press = TRIGGER_TYPES[key]
    return f"{CONTROL_LABELS[control]} {PRESS_LABELS[press]}"


ACTION_HELP = "Runs when this happens. Leave empty to do nothing (the event still fires)."
GROUP_TEXT = {
    "navigation": (
        "Navigation",
        "Touchpad swipes and clicks, Back and the TV button. Holding a direction repeats its action (see Timing).",
    ),
    "volume_power": (
        "Volume & power",
        "The phone's volume keys, plus Mute and Power from the remote. Holding a volume key repeats its action.",
    ),
    "media": ("Media", "Play/Pause, the ±10 s skip buttons and Siri."),
}

mapping_steps = {
    group: {
        "title": GROUP_TEXT[group][0],
        "description": GROUP_TEXT[group][1],
        "data": {key: mapping_label(key) for key in keys},
        "data_description": {key: ACTION_HELP for key in keys},
    }
    for group, keys in MAPPING_GROUPS.items()
}
input_data = {
    "input_mode": "Mode",
    "input_long": "Action (single mode)",
}
input_desc = {
    "input_mode": "Single: run one action, e.g. open the TV's input menu. Cycle: each long press moves to the next input below.",
    "input_long": "Runs on a long press of the TV button in single mode.",
}
for n in range(1, MAX_INPUT_SOURCES + 1):
    input_data[f"source_{n}_name"] = f"Input {n} name"
    input_data[f"source_{n}_actions"] = f"Input {n} action"

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
        "error": {
            "invalid_actions": "These actions are not valid.",
            "no_sources": "Add at least one input to use cycle mode.",
        },
        "step": {
            "init": {
                "title": "Remote Bridge options",
                "menu_options": {
                    **{group: text[0] for group, text in GROUP_TEXT.items()},
                    "input": "Input / source (TV button held)",
                    "timing": "Timing",
                    "pairing": "Pairing",
                },
            },
            **mapping_steps,
            "input": {
                "title": "Input / source",
                "description": "What a long press of the TV button does.",
                "data": input_data,
                "data_description": input_desc,
            },
            "timing": {
                "title": "Timing",
                "data": {
                    "long_press_ms": "Long press",
                    "double_tap_ms": "Double-press window",
                    "repeat_delay_ms": "Repeat starts after",
                    "repeat_interval_ms": "Repeat every",
                    "swipe_threshold": "Swipe distance",
                    "repeat_controls": "Repeat while held",
                    "home_double_press": "Detect TV button double-press",
                },
                "data_description": {
                    "long_press_ms": "How long to hold Select, Back, Siri or the TV button for a long press.",
                    "double_tap_ms": "Only used by the TV button double-press.",
                    "repeat_controls": "IR TVs often handle repeats badly; turn off any that misbehave.",
                    "swipe_threshold": "Touchpad units (0-1000). Lower = shorter swipes count.",
                    "home_double_press": "On automatically when a double-press action is set. Delays a single press by the window.",
                },
            },
            "pairing": {
                "title": "Pairing",
                "data": {"allow_pairing": "Allow new pairings"},
                "data_description": {
                    "allow_pairing": (
                        "Lets a new phone pair. Switches itself off after a successful pairing."
                    )
                },
            },
        },
    },
    "selector": {
        "input_mode": {"options": {"single": "Single action", "cycle": "Cycle through inputs"}},
        "repeat_controls": {"options": {c: CONTROL_LABELS[c] for c in REPEAT_CAPABLE}},
    },
    "entity": {
        "binary_sensor": {"connected": {"name": "Connected"}},
        "sensor": {"last_button": {"name": "Last button"}},
        "button": {"forget_paired_devices": {"name": "Forget paired devices"}},
        "select": {"input_source": {"name": "Input source"}},
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
