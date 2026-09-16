import type { ChannelUiContribution } from "@/channel-plugins/types";
import { chatAppGuideUrl } from "@/components/settings/channels/catalog";

import { WebSocketIcon } from "./WebSocketIcon";

export default {
  presentation: {
    displayName: "ruiclaw WebUI",
    initials: "WS",
    color: "#111827",
    icon: WebSocketIcon,
    setup: {
      mode: "webui",
      docsUrl: chatAppGuideUrl("websocket"),
    },
  },
} satisfies ChannelUiContribution;
