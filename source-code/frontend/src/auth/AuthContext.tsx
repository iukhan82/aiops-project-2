import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, api, setSessionEndedHandler } from "../api/client";
import { keycloak, initAuth, markSession, providerReachable, signOut as keycloakSignOut } from "./keycloak";

export interface Me {
  sub: string;
  username: string;
  name: string;
  roles: string[];
  capabilities: string[];
  home: string | null;
  expires_at: number;
  auth_mode: string;
}

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous"; reason: "none" | "expired" | "unreachable" | "error"; message?: string }
  | { status: "authenticated"; me: Me; capabilities: ReadonlySet<string> };

interface AuthApi {
  state: AuthState;
  signOut: () => Promise<void>;
  can: (capability: string | null | undefined) => boolean;
}

const AuthContext = createContext<AuthApi | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    // A token that cannot be renewed is one of two different problems: the session ended (sign in again) or the identity provider
    // cannot be reached (wait, then try again). The screen says which, and keeps the destination either way.
    const expire = () => {
      void providerReachable().then((reachable) => {
        if (reachable) markSession(false);
        setState({ status: "anonymous", reason: reachable ? "expired" : "unreachable" });
      });
    };
    setSessionEndedHandler(expire);
    keycloak.onAuthRefreshError = expire;
    keycloak.onTokenExpired = () => {
      keycloak.updateToken(30).catch(expire);
    };
    (async () => {
      try {
        const authenticated = await initAuth();
        if (!authenticated) {
          markSession(false);
          if (!cancelled) setState({ status: "anonymous", reason: "none" });
          return;
        }
        const me = await api<Me>("/api/v1/me");
        markSession(true);
        if (!cancelled) setState({ status: "authenticated", me, capabilities: new Set(me.capabilities) });
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 403) {
          setState({ status: "anonymous", reason: "error", message: "Your account is signed in but holds no operating role for this system. Ask an administrator to assign one." });
        } else if (error instanceof ApiError && error.code === "session_ended") {
          setState({ status: "anonymous", reason: "expired" });
        } else if (error instanceof ApiError) {
          setState({ status: "anonymous", reason: "error", message: `Signed in, but the operations API answered: ${error.message}.` });
        } else {
          setState({ status: "anonymous", reason: "error", message: "Sign-in could not be completed. The identity provider may be unreachable." });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const signOut = useCallback(() => keycloakSignOut(), []);
  const value = useMemo<AuthApi>(
    () => ({
      state,
      signOut,
      can: (capability) => capability == null || (state.status === "authenticated" && state.capabilities.has(capability)),
    }),
    [state, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthApi {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}

export function useMe(): Me {
  const { state } = useAuth();
  if (state.status !== "authenticated") throw new Error("useMe outside an authenticated screen");
  return state.me;
}
