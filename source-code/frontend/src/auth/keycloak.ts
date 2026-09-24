import Keycloak from "keycloak-js";

const KEYCLOAK_URL = import.meta.env.VITE_KEYCLOAK_URL ?? "http://localhost:8180";
const RETURN_TO_KEY = "aiops.returnTo";
const SIGNED_OUT_KEY = "aiops.signedOut";

export const keycloak = new Keycloak({ url: KEYCLOAK_URL, realm: "aiops", clientId: "aiops-ui" });

let initialised: Promise<boolean> | null = null;

const HAD_SESSION_KEY = "aiops.hadSession";

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** The token lives only in memory, so a reload starts signed out. If this tab had a session, re-check the identity
 * provider once (a redirect that needs no password while its single-sign-on session lives) instead of asking again. */
export function markSession(active: boolean): void {
  const store = storage();
  if (!store) return;
  if (active) store.setItem(HAD_SESSION_KEY, "1");
  else store.removeItem(HAD_SESSION_KEY);
}

/** Authorization Code with PKCE (S256). Called once; React StrictMode's second effect run reuses the promise. */
export async function initAuth(): Promise<boolean> {
  const onCallback = window.location.pathname === "/callback";
  const recheck = storage()?.getItem(HAD_SESSION_KEY) === "1" && !onCallback;
  if (recheck) {
    rememberReturnTo(window.location.pathname + window.location.search);
    // The re-check is a redirect to the identity provider. If it does not answer, that would leave the person on the browser's own
    // error page with the destination lost, so it is not attempted; the sign-in screen says why instead (and is not cached: a later
    // attempt tries again).
    if (!(await providerReachable())) throw new Error("the identity provider cannot be reached");
  }
  initialised ??= keycloak.init({
    pkceMethod: "S256",
    checkLoginIframe: false,
    flow: "standard",
    enableLogging: false,
    redirectUri: `${window.location.origin}/callback`,
    ...(recheck ? { onLoad: "check-sso" as const } : {}),
  });
  return initialised;
}

export function rememberReturnTo(path: string): void {
  try {
    sessionStorage.setItem(RETURN_TO_KEY, path);
  } catch {
    /* storage may be blocked; the user then lands on their home screen */
  }
}

export function takeReturnTo(): string | null {
  try {
    const value = sessionStorage.getItem(RETURN_TO_KEY);
    sessionStorage.removeItem(RETURN_TO_KEY);
    return value && value.startsWith("/") && !value.startsWith("//") ? value : null;
  } catch {
    return null;
  }
}

export async function signIn(): Promise<void> {
  if (!keycloak.didInitialize) await initAuth();
  return keycloak.login({ redirectUri: `${window.location.origin}/callback` });
}

export function signOut(): Promise<void> {
  markSession(false);
  try {
    sessionStorage.setItem(SIGNED_OUT_KEY, "1");
  } catch {
    /* the login page then simply does not say "signed out" */
  }
  return keycloak.logout({ redirectUri: `${window.location.origin}/login` });
}

export function peekSignedOutFlag(): boolean {
  try {
    return sessionStorage.getItem(SIGNED_OUT_KEY) === "1";
  } catch {
    return false;
  }
}

export function clearSignedOutFlag(): void {
  try {
    sessionStorage.removeItem(SIGNED_OUT_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Asks the identity provider whether it answers at all. Told apart from "the session ended": the fix for the first is to wait, for the second to sign in. */
export async function providerReachable(): Promise<boolean> {
  try {
    const response = await fetch(`${KEYCLOAK_URL}/realms/aiops/.well-known/openid-configuration`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    return response.ok;
  } catch {
    return false;
  }
}

/** A token valid for at least `minValiditySeconds`, refreshed if needed. Throws if the session cannot be refreshed. */
export async function freshToken(minValiditySeconds = 30): Promise<string> {
  await keycloak.updateToken(minValiditySeconds);
  if (!keycloak.token) throw new Error("no token");
  return keycloak.token;
}

export async function forceRefresh(): Promise<string> {
  await keycloak.updateToken(-1);
  if (!keycloak.token) throw new Error("no token");
  return keycloak.token;
}
