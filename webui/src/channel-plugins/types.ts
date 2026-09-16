import type { ComponentType } from "react";

import type { ChannelPresentation } from "@/components/settings/channels/catalog";
import type {
  RuiClawFeatureInfo,
  RuiClawFeaturesPayload,
} from "@/lib/types";

export type ChannelFeatureActionOptions = {
  confirmed?: boolean;
  installOnly?: boolean;
};

export type ChannelFeatureAction = (
  action: "enable" | "disable",
  name: string,
  options?: ChannelFeatureActionOptions,
) => void;

export type ChannelPluginPanelProps = {
  connectRequestId?: number;
  token: string;
  feature: RuiClawFeatureInfo;
  actionKey: string | null;
  showBrandLogos: boolean;
  onAction: ChannelFeatureAction;
  onFeaturesUpdate: (payload: RuiClawFeaturesPayload) => void;
};

export type ChannelPluginConnectFlowProps = {
  token: string;
  feature: RuiClawFeatureInfo;
  idleLabel?: string;
  connectRequestId?: number;
  onFeaturesUpdate: (payload: RuiClawFeaturesPayload) => void;
};

export type ChannelUiContribution = {
  presentation: ChannelPresentation;
  aliases?: Record<string, Partial<ChannelPresentation>>;
  Panel?: ComponentType<ChannelPluginPanelProps>;
  ConnectFlow?: ComponentType<ChannelPluginConnectFlowProps>;
  canConnectBeforeConfigured?: boolean;
};

export type RegisteredChannelUiContribution = {
  channel: string;
  webui: string;
  contribution: ChannelUiContribution;
};
