import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { clearSignedOutFlag, peekSignedOutFlag, providerReachable, rememberReturnTo, signIn, takeReturnTo } from "../auth/keycloak";
import { AppShell, PublicShell } from "../components/AppShell";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { Page } from "../components/Page";
import { ForbiddenState, LoadingState } from "../components/StateViews";
import { homePathFor, screenById } from "../config/access";

/** Layout route for every screen behind sign-in. */
export function RequireAuth() {
  const { state } = useAuth();
  const location = useLocation();
  const intended = location.pathname + location.search;
  const anonymous = state.status === "anonymous";
  useEffect(() => {
    if (anonymous) rememberReturnTo(intended);
  }, [anonymous, intended]);
  if (state.status === "loading") {
    return (
      <PublicShell>
        <Page title="Signing you in">
          <LoadingState what="your session" />
        </Page>
      </PublicShell>
    );
  }
  if (state.status === "anonymous") return <Navigate to={state.reason === "expired" || state.reason === "unreachable" ? "/session-expired" : "/login"} replace />;
  return <AppShell />;
}

export function HomeRedirect() {
  const { state } = useAuth();
  if (state.status !== "authenticated") return <Navigate to="/login" replace />;
  return <Navigate to={homePathFor(state.me.home)} replace />;
}

export function RequireCapability({ screenId, children }: { screenId: string; children: React.ReactNode }) {
  const { state } = useAuth();
  const screen = screenById(screenId);
  if (!screen || state.status !== "authenticated") return null;
  if (screen.capability !== null && !state.capabilities.has(screen.capability)) {
    return <Navigate to={`/forbidden?screen=${encodeURIComponent(screen.id)}&capability=${encodeURIComponent(screen.capability)}`} replace />;
  }
  return <>{children}</>;
}

/** Sends a freshly signed-in user to where they were going. Runs once even when React re-runs effects in development. */
function GoToDestination({ home }: { home: string }) {
  const navigate = useNavigate();
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    navigate(takeReturnTo() ?? home, { replace: true });
  }, [home, navigate]);
  return null;
}

export function LoginPage() {
  const { state } = useAuth();
  const [signedOut] = useState(peekSignedOutFlag);
  const [stillDown, setStillDown] = useState(false);
  useEffect(() => clearSignedOutFlag(), []);
  if (state.status === "authenticated") return <GoToDestination home={homePathFor(state.me.home)} />;
  return (
    <PublicShell>
      <Page title="Sign in" subtitle="Traffic Operations console for the simulated city network.">
        <div className="panel narrow">
          {signedOut ? <Banner>You have been signed out.</Banner> : null}
          {state.status === "anonymous" && state.reason === "error" ? <Banner tone="danger" alert>{state.message}</Banner> : null}
          {stillDown ? <Banner tone="danger" alert>The sign-in service still cannot be reached. Try again in a moment.</Banner> : null}
          <p>
            You sign in on your organisation's identity service. This console never sees your password and only receives a short-lived token that says which operating role you hold.
          </p>
          <Button
            variant="primary"
            disabled={state.status === "loading"}
            onClick={() => {
              setStillDown(false);
              signIn().catch(() => setStillDown(true));
            }}
          >
            Sign in
          </Button>
        </div>
      </Page>
    </PublicShell>
  );
}

/** Where the identity service sends the browser back to. keycloak-js has already exchanged the code by the time this renders. */
export function CallbackPage() {
  const { state } = useAuth();
  const navigate = useNavigate();
  useEffect(() => {
    if (state.status === "anonymous") navigate("/login", { replace: true });
  }, [state, navigate]);
  return state.status === "authenticated" ? (
    <GoToDestination home={homePathFor(state.me.home)} />
  ) : (
    <PublicShell>
      <Page title="Completing sign-in">
        <LoadingState what="your session" />
      </Page>
    </PublicShell>
  );
}

export function SessionExpiredPage() {
  const { state } = useAuth();
  const [checking, setChecking] = useState(false);
  const [stillDown, setStillDown] = useState(false);
  if (state.status === "authenticated") return <GoToDestination home={homePathFor(state.me.home)} />;
  const unreachable = state.status === "anonymous" && state.reason === "unreachable";
  const retry = async () => {
    setChecking(true);
    setStillDown(false);
    if (await providerReachable()) {
      await signIn();
    } else {
      setStillDown(true);
      setChecking(false);
    }
  };
  return (
    <PublicShell>
      <Page title={unreachable ? "Sign-in service unreachable" : "Session ended"}>
        <div className="panel narrow">
          {unreachable ? (
            <Banner tone="danger">The sign-in service cannot be reached, so your session could not be renewed. Nothing you see here is live any more. Try again in a moment; you will come back to where you were.</Banner>
          ) : (
            <Banner tone="warn">Your session ended, so nothing you see here is live any more. Sign in again to carry on where you were.</Banner>
          )}
          {stillDown ? <Banner tone="danger" alert>The sign-in service still cannot be reached.</Banner> : null}
          <Button variant="primary" busy={checking} onClick={() => void (unreachable ? retry() : signIn().catch(() => setStillDown(true)))}>
            {unreachable ? "Try again" : "Sign in again"}
          </Button>
        </div>
      </Page>
    </PublicShell>
  );
}

export function ForbiddenPage() {
  const [params] = useSearchParams();
  const screen = screenById(params.get("screen") ?? "");
  return (
    <Page title="Not permitted">
      <ForbiddenState capability={params.get("capability") ?? screen?.capability ?? null} screen={screen?.title} />
    </Page>
  );
}

export function NotFoundPage() {
  const { state } = useAuth();
  const body = (
    <Page title="Page not found">
      <div role="status" className="state">
        <h2>There is nothing at this address</h2>
        <p>
          <Link to="/">Go to {state.status === "authenticated" ? "your home screen" : "sign in"}</Link>
        </p>
      </div>
    </Page>
  );
  return state.status === "authenticated" ? <AppShell>{body}</AppShell> : <PublicShell>{body}</PublicShell>;
}
