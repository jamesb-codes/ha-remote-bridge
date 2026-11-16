"""Phase 4: mapped actions, options pages, cycle mode, live updates."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.remote_bridge.const import (
    CONF_ALLOW_PAIRING,
    CONF_HOME_DOUBLE,
    CONF_INPUT_MODE,
    CONF_INPUT_SOURCES,
    CONF_LONG_PRESS_MS,
    CONF_MAPPINGS,
    CONF_REPEAT_CONTROLS,
    CONF_SWIPE_THRESHOLD,
    DOMAIN,
)
from custom_components.remote_bridge.gesture import ControlEvent, PressType

pytestmark = pytest.mark.usefixtures("socket_enabled")

SELECT = "select.test_tv_input_source"


def call(service: str, **data: Any) -> list[dict[str, Any]]:
    return [{"action": f"test.{service}", "data": data}]


async def setup(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test TV",
        data={CONF_NAME: "Test TV", CONF_PORT: 0},
        options={CONF_ALLOW_PAIRING: False, **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def press(
    hass: HomeAssistant, entry: MockConfigEntry, control: str, kind: PressType, value: Any = None
) -> None:
    entry.runtime_data.on_control(ControlEvent(control, kind, "Phone", 0.0, value))
    await hass.async_block_till_done()


async def options_page(hass: HomeAssistant, entry: MockConfigEntry, page: str, data: dict[str, Any]) -> Any:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": page})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == page
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result


async def test_mapped_action_runs_with_variables(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "test", "press")
    entry = await setup(
        hass,
        {
            CONF_MAPPINGS: {
                "up_short": call(
                    "press",
                    control="{{ control }}",
                    kind="{{ press_type }}",
                    tv="{{ virtual_tv }}",
                    who="{{ client }}",
                ),
                "skip_forward_short": call("press", seconds="{{ value }}"),
            }
        },
    )
    await press(hass, entry, "up", PressType.SHORT)
    await press(hass, entry, "down", PressType.SHORT)  # unmapped: nothing runs
    await press(hass, entry, "skip_forward", PressType.SHORT, 10.0)
    assert [c.data for c in calls] == [
        {"control": "up", "kind": "short", "tv": "Test TV", "who": "Phone"},
        {"seconds": 10.0},
    ]


async def test_repeat_falls_back_to_short(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "test", "press")
    entry = await setup(hass, {CONF_MAPPINGS: {"volume_up_short": call("press", step="{{ press_type }}")}})
    await press(hass, entry, "volume_up", PressType.SHORT)
    await press(hass, entry, "volume_up", PressType.REPEAT)
    await press(hass, entry, "volume_up", PressType.REPEAT)
    assert [c.data["step"] for c in calls] == ["short", "repeat", "repeat"]


async def test_options_page_applies_live_without_reload(hass: HomeAssistant) -> None:
    old = async_mock_service(hass, "test", "old")
    new = async_mock_service(hass, "test", "new")
    entry = await setup(hass, {CONF_MAPPINGS: {"select_short": call("old"), "power_short": call("old")}})
    server = entry.runtime_data.server

    result = await options_page(
        hass, entry, "navigation", {"select_short": call("new"), "back_long": call("new")}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # power_short belongs to another page and must survive; navigation keys replaced.
    assert set(entry.options[CONF_MAPPINGS]) == {"select_short", "back_long", "power_short"}
    assert entry.runtime_data.server is server  # not reloaded: phones stay connected

    await press(hass, entry, "select", PressType.SHORT)
    await press(hass, entry, "back", PressType.LONG)
    await press(hass, entry, "power", PressType.SHORT)
    assert len(new) == 2
    assert len(old) == 1


async def test_clearing_a_mapping(hass: HomeAssistant) -> None:
    entry = await setup(hass, {CONF_MAPPINGS: {"mute_short": call("x")}})
    await options_page(hass, entry, "volume_power", {})
    assert entry.options[CONF_MAPPINGS] == {}


async def test_invalid_actions_show_error(hass: HomeAssistant) -> None:
    entry = await setup(hass, {})
    result = await options_page(hass, entry, "media", {"play_pause_short": [{"not_an_action": 1}]})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"play_pause_short": "invalid_actions"}


async def test_failing_action_is_logged_not_raised(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    calls = async_mock_service(hass, "test", "ok")
    entry = await setup(
        hass, {CONF_MAPPINGS: {"left_short": call("does_not_exist"), "right_short": call("ok")}}
    )
    await press(hass, entry, "left", PressType.SHORT)
    await press(hass, entry, "right", PressType.SHORT)
    assert "action Test TV left_short failed" in caplog.text
    assert len(calls) == 1


async def test_invalid_stored_actions_are_skipped(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    await setup(hass, {CONF_MAPPINGS: {"left_short": [{"bogus": True}]}})
    assert "ignoring invalid actions for left_short" in caplog.text


async def test_input_single_mode(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "test", "menu")
    entry = await setup(hass, {})
    result = await options_page(hass, entry, "input", {CONF_INPUT_MODE: "single", "input_long": call("menu")})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await press(hass, entry, "input", PressType.LONG)
    assert len(calls) == 1
    assert hass.states.get(SELECT).state == "unavailable"


async def test_input_cycle_wraps_and_is_settable(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "test", "source")
    entry = await setup(hass, {})
    result = await options_page(
        hass,
        entry,
        "input",
        {
            CONF_INPUT_MODE: "cycle",
            "source_1_name": "HDMI 1",
            "source_1_actions": call("source", n=1),
            "source_2_name": "",
            "source_2_actions": call("source", n=2),
            "source_3_name": "TV",
            "source_3_actions": call("source", n=3),
            "source_4_name": "No actions, ignored",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [s["name"] for s in entry.options[CONF_INPUT_SOURCES]] == ["HDMI 1", "Input 2", "TV"]
    state = hass.states.get(SELECT)
    assert state.state == "HDMI 1" and state.attributes["options"] == ["HDMI 1", "Input 2", "TV"]

    for _ in range(4):
        await press(hass, entry, "input", PressType.LONG)
    assert [c.data["n"] for c in calls] == [2, 3, 1, 2]  # wraps round
    assert hass.states.get(SELECT).state == "Input 2"

    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: SELECT, "option": "TV"}, blocking=True
    )
    assert len(calls) == 4  # picking only moves the pointer
    await press(hass, entry, "input", PressType.LONG)
    assert calls[-1].data["n"] == 1


async def test_cycle_needs_sources(hass: HomeAssistant) -> None:
    entry = await setup(hass, {})
    result = await options_page(hass, entry, "input", {CONF_INPUT_MODE: "cycle"})
    assert result["errors"] == {CONF_INPUT_MODE: "no_sources"}


async def test_cycle_index_restored_after_restart(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "test", "source")
    mock_restore_cache(hass, [State(SELECT, "Game")])
    entry = await setup(
        hass,
        {
            CONF_INPUT_MODE: "cycle",
            CONF_INPUT_SOURCES: [
                {"name": "HDMI 1", "actions": call("source", n=1)},
                {"name": "Game", "actions": call("source", n=2)},
                {"name": "TV", "actions": call("source", n=3)},
            ],
        },
    )
    assert hass.states.get(SELECT).state == "Game"
    await press(hass, entry, "input", PressType.LONG)
    assert calls[-1].data["n"] == 3


async def test_timing_page_updates_gestures_live(hass: HomeAssistant) -> None:
    entry = await setup(hass, {})
    runtime = entry.runtime_data
    assert runtime.engine.options.double_controls == frozenset()  # TV single press stays instant

    result = await options_page(
        hass,
        entry,
        "timing",
        {
            CONF_LONG_PRESS_MS: 800,
            "double_tap_ms": 400,
            "repeat_delay_ms": 600,
            "repeat_interval_ms": 300,
            CONF_SWIPE_THRESHOLD: 90,
            CONF_REPEAT_CONTROLS: ["volume_up", "volume_down"],
            CONF_HOME_DOUBLE: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    opts = runtime.engine.options
    assert (opts.long_press_ms, opts.double_tap_ms, opts.repeat_delay_ms, opts.repeat_interval_ms) == (
        800,
        400,
        600,
        300,
    )
    assert opts.repeat_controls == frozenset({"volume_up", "volume_down"})
    assert runtime.server.swipe_threshold == 90

    await options_page(hass, entry, "navigation", {"home_double_double": call("x")})
    assert runtime.engine.options.double_controls == frozenset({"home"})  # enabled by mapping
