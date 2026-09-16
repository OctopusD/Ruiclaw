import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

import type { RuiClawClient } from "@/lib/ruiclaw-client";
import type { WebUIIngressLimits } from "@/lib/types";

interface ClientContextValue {
  client: RuiClawClient;
  token: string;
  getToken: () => string;
  modelName: string | null;
  ingressLimits: WebUIIngressLimits | null;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  ingressLimits = null,
  children,
}: {
  client: RuiClawClient;
  token: string;
  modelName?: string | null;
  ingressLimits?: WebUIIngressLimits | null;
  children: ReactNode;
}) {
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const getToken = useCallback(() => tokenRef.current, []);
  const value = useMemo(
    () => ({ client, token, getToken, modelName, ingressLimits }),
    [client, getToken, ingressLimits, modelName, token],
  );

  return (
    <ClientContext.Provider value={value}>
      {children}
    </ClientContext.Provider>
  );
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}
