from types import SimpleNamespace

from ruiclaw.cli.webui_support import _prepare_webui_bundle_for_gateway
from ruiclaw.config.schema import Config


def test_source_checkout_preserves_warn_only_gateway_startup(monkeypatch) -> None:
    modes: list[str] = []
    monkeypatch.setattr(
        "ruiclaw.cli.webui_support.inspect_webui_bundle",
        lambda: SimpleNamespace(source_available=True),
    )
    monkeypatch.setattr("ruiclaw.cli.webui_support._webui_channel_enabled", lambda _config: True)
    monkeypatch.setattr(
        "ruiclaw.cli.webui_support.ensure_webui_bundle",
        lambda **kwargs: modes.append(kwargs["mode"]),
    )

    _prepare_webui_bundle_for_gateway(Config(), mode="warn")

    assert modes == ["warn"]


def test_skip_mode_does_not_build_the_source_webui_bundle(monkeypatch) -> None:
    modes: list[str] = []
    monkeypatch.setattr(
        "ruiclaw.cli.webui_support.inspect_webui_bundle",
        lambda: SimpleNamespace(source_available=True),
    )
    monkeypatch.setattr("ruiclaw.cli.webui_support._webui_channel_enabled", lambda _config: True)
    monkeypatch.setattr(
        "ruiclaw.cli.webui_support.ensure_webui_bundle",
        lambda **kwargs: modes.append(kwargs["mode"]),
    )

    _prepare_webui_bundle_for_gateway(Config(), mode="skip")

    assert modes == ["skip"]
