"""WhatsApp management contract."""

from ruiclaw.channels._manifest import DIRECT_GROUP_POLICIES, field
from ruiclaw.channels.contracts import ChannelManagementSpec, ChannelSetupSpec
from ruiclaw.channels.plugin import ChannelPlugin
from ruiclaw.channels.whatsapp.state import local_state_present
from ruiclaw.channels.whatsapp.validation import validate

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "proxy": field(),
        "allowFrom": field("list", snapshot=False),
        "groupPolicy": field(
            "enum",
            choices=DIRECT_GROUP_POLICIES,
            default="open",
            snapshot=False,
        ),
        "databasePath": field(writable=False, snapshot=False),
        "lidMappings": field("json", writable=False, snapshot=False),
    },
    official_url="https://faq.whatsapp.com/",
    validator=validate,
)

PLUGIN = ChannelPlugin(
    name="whatsapp",
    display_name="WhatsApp",
    runtime=f"{__package__}.runtime:WhatsAppChannel",
    connector=f"{__package__}.connect:WhatsAppConnectStore",
    setup=SETUP_SPEC,
    management=ChannelManagementSpec(local_state_present=local_state_present),
    dependencies=(
        "neonize>=0.4.3.post0,<0.5.0",
        "segno>=1.6.1,<2.0.0",
    ),
    webui="webui/index.tsx",
)
