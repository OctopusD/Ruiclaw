"""WeCom management contract."""

from ruiclaw.channels._manifest import field, required_fields
from ruiclaw.channels.contracts import ChannelSetupSpec
from ruiclaw.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "botId": field(),
        "secret": field("secret"),
        "allowFrom": field("list"),
        "welcomeMessage": field(),
    },
    required=required_fields("botId", "secret"),
    official_url="https://developer.work.weixin.qq.com/",
)

PLUGIN = ChannelPlugin(
    name="wecom",
    display_name="WeCom",
    runtime=f"{__package__}.runtime:WecomChannel",
    setup=SETUP_SPEC,
    dependencies=("wecom-aibot-sdk-python>=0.1.7,<0.2.0",),
    webui="webui/index.ts",
)
