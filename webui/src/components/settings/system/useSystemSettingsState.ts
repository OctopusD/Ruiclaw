import { useRef, useState } from "react";

import type {
  AutomationFilter,
  AutomationSort,
} from "@/components/settings/system/AutomationsSettings";
import {
  DEFAULT_CUSTOM_MCP_FORM,
  type AppsKindFilter,
  type CustomMcpForm,
} from "@/components/settings/system/AppsSettings";
import type {
  ApiServicePayload,
  AutomationsPayload,
  CliAppsPayload,
  McpOAuthFlowPayload,
  McpPresetsPayload,
  RuiClawFeatureInfo,
  RuiClawFeaturesPayload,
  SessionAutomationJob,
} from "@/lib/types";

export type RuiClawFeatureInstallRequest = {
  feature: RuiClawFeatureInfo;
  installOnly: boolean;
};

export function useSystemSettingsState() {
  const [cliApps, setCliApps] = useState<CliAppsPayload | null>(null);
  const [ruiclawFeatures, setRuiClawFeatures] = useState<RuiClawFeaturesPayload | null>(null);
  const [mcpPresets, setMcpPresets] = useState<McpPresetsPayload | null>(null);
  const [automations, setAutomations] = useState<AutomationsPayload | null>(null);
  const [cliAppsLoading, setCliAppsLoading] = useState(true);
  const [ruiclawFeaturesLoading, setRuiClawFeaturesLoading] = useState(true);
  const [mcpPresetsLoading, setMcpPresetsLoading] = useState(true);
  const [automationsLoading, setAutomationsLoading] = useState(false);
  const [cliAppsAction, setCliAppsAction] = useState<string | null>(null);
  const [ruiclawFeatureAction, setRuiClawFeatureAction] = useState<string | null>(null);
  const ruiclawFeatureActionRef = useRef<string | null>(null);
  const [ruiclawFeatureConfirm, setRuiClawFeatureConfirm] =
    useState<RuiClawFeatureInstallRequest | null>(null);
  const [mcpPresetAction, setMcpPresetAction] = useState<string | null>(null);
  const [mcpOAuthFlow, setMcpOAuthFlow] = useState<McpOAuthFlowPayload | null>(null);
  const mcpOAuthFlowRef = useRef<McpOAuthFlowPayload | null>(null);
  const mcpOAuthPopupRef = useRef<Window | null>(null);
  const mcpOAuthNavigatedUrlRef = useRef<string | null>(null);
  const [mcpOAuthPopupBlocked, setMcpOAuthPopupBlocked] = useState(false);
  const [mcpOAuthCallbackUrl, setMcpOAuthCallbackUrl] = useState("");
  const [mcpOAuthCompleting, setMcpOAuthCompleting] = useState(false);
  const [mcpOAuthCallbackError, setMcpOAuthCallbackError] = useState<string | null>(null);
  const [apiService, setApiService] = useState<ApiServicePayload | null>(null);
  const [apiServiceLoading, setApiServiceLoading] = useState(false);
  const [apiServiceAction, setApiServiceAction] = useState<"start" | "stop" | null>(null);
  const [apiServiceError, setApiServiceError] = useState<string | null>(null);
  const [appsQuery, setAppsQuery] = useState("");
  const [automationsQuery, setAutomationsQuery] = useState("");
  const [automationsFilter, setAutomationsFilter] = useState<AutomationFilter>("all");
  const [automationsSort, setAutomationsSort] = useState<AutomationSort>("next");
  const [cliAppsMessage, setCliAppsMessage] = useState<string | null>(null);
  const [cliAppsError, setCliAppsError] = useState<string | null>(null);
  const [ruiclawFeaturesError, setRuiClawFeaturesError] = useState<string | null>(null);
  const [cliAppsFocusName, setCliAppsFocusName] = useState<string | null>(null);
  const [appsKindFilter, setAppsKindFilter] = useState<AppsKindFilter>("cli");
  const [mcpMessage, setMcpMessage] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [automationsError, setAutomationsError] = useState<string | null>(null);
  const [automationAction, setAutomationAction] = useState<string | null>(null);
  const [automationPendingDelete, setAutomationPendingDelete] =
    useState<SessionAutomationJob | null>(null);
  const [automationPendingEdit, setAutomationPendingEdit] =
    useState<SessionAutomationJob | null>(null);
  const [mcpFieldValues, setMcpFieldValues] = useState<Record<string, Record<string, string>>>({});
  const [customMcpForm, setCustomMcpForm] = useState<CustomMcpForm>(DEFAULT_CUSTOM_MCP_FORM);
  const [mcpConfigImport, setMcpConfigImport] = useState("");

  return {
    apiService,
    apiServiceAction,
    apiServiceError,
    apiServiceLoading,
    appsKindFilter,
    appsQuery,
    automationAction,
    automationPendingDelete,
    automationPendingEdit,
    automations,
    automationsError,
    automationsFilter,
    automationsLoading,
    automationsQuery,
    automationsSort,
    cliApps,
    cliAppsAction,
    cliAppsError,
    cliAppsFocusName,
    cliAppsLoading,
    cliAppsMessage,
    customMcpForm,
    mcpConfigImport,
    mcpError,
    mcpFieldValues,
    mcpMessage,
    mcpOAuthCallbackError,
    mcpOAuthCallbackUrl,
    mcpOAuthCompleting,
    mcpOAuthFlow,
    mcpOAuthFlowRef,
    mcpOAuthNavigatedUrlRef,
    mcpOAuthPopupBlocked,
    mcpOAuthPopupRef,
    mcpPresetAction,
    mcpPresets,
    mcpPresetsLoading,
    ruiclawFeatureAction,
    ruiclawFeatureActionRef,
    ruiclawFeatureConfirm,
    ruiclawFeatures,
    ruiclawFeaturesError,
    ruiclawFeaturesLoading,
    setApiService,
    setApiServiceAction,
    setApiServiceError,
    setApiServiceLoading,
    setAppsKindFilter,
    setAppsQuery,
    setAutomationAction,
    setAutomationPendingDelete,
    setAutomationPendingEdit,
    setAutomations,
    setAutomationsError,
    setAutomationsFilter,
    setAutomationsLoading,
    setAutomationsQuery,
    setAutomationsSort,
    setCliApps,
    setCliAppsAction,
    setCliAppsError,
    setCliAppsFocusName,
    setCliAppsLoading,
    setCliAppsMessage,
    setCustomMcpForm,
    setMcpConfigImport,
    setMcpError,
    setMcpFieldValues,
    setMcpMessage,
    setMcpOAuthCallbackError,
    setMcpOAuthCallbackUrl,
    setMcpOAuthCompleting,
    setMcpOAuthFlow,
    setMcpOAuthPopupBlocked,
    setMcpPresetAction,
    setMcpPresets,
    setMcpPresetsLoading,
    setRuiClawFeatureAction,
    setRuiClawFeatureConfirm,
    setRuiClawFeatures,
    setRuiClawFeaturesError,
    setRuiClawFeaturesLoading,
  };
}

export type SystemSettingsState = ReturnType<typeof useSystemSettingsState>;
