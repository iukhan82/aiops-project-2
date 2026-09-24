import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { navigationFor, screenById, type ScreenDef } from "../config/access";
import { words } from "../lib/format";
import { Button } from "./Button";
import { ErrorBoundary } from "./ErrorBoundary";
import { LiveFeedStatus } from "./Feed";
import { Shape } from "./Shape";

const QUICK_PRIORITY = ["field", "incidents", "map", "handover", "dispatch"];

function SkipLink() {
  return (
    <a
      className="skip-link"
      href="#main"
      onClick={(event) => {
        event.preventDefault();
        document.getElementById("main")?.focus();
      }}
    >
      Skip to main content
    </a>
  );
}

/** Present only for the demo identity: it says, on every screen, that the signed-in identity is the separate one. */
function DemoTag() {
  const { state } = useAuth();
  if (state.status !== "authenticated" || !state.capabilities.has("demo.control")) return null;
  return (
    <span className="demo-tag" title="You are signed in as the demo operator: a separate identity for the scenario-control service, with no operational authority.">
      <Shape name="diamond" size={11} />
      DEMO IDENTITY
    </span>
  );
}

function Header({ children }: { children?: ReactNode }) {
  return (
    <header className="app-header" role="banner">
      <Link to="/" className="brand">
        Traffic Operations
      </Link>
      <LiveFeedStatus />
      <span className="sim-tag" title="Every value on these screens comes from the simulator, not from live roadside equipment.">
        <Shape name="hexagon" size={11} />
        SIMULATED
      </span>
      <DemoTag />
      {children}
    </header>
  );
}

function UserMenu() {
  const { state, signOut } = useAuth();
  if (state.status !== "authenticated") return null;
  return (
    <div className="user-menu">
      <span className="user-name" data-testid="user-name">
        {state.me.name || state.me.username}
        <span className="user-role" data-testid="user-role">
          {state.me.roles.map(words).join(", ")}
        </span>
      </span>
      <Button variant="secondary" onClick={() => void signOut()}>
        Sign out
      </Button>
    </div>
  );
}

function Navigation({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const { state } = useAuth();
  if (state.status !== "authenticated") return null;
  const groups = navigationFor(state.capabilities);
  return (
    <nav aria-label="Primary" id="primary-nav" className={open ? "nav open" : "nav"}>
      {groups.map((group) => (
        <div className="nav-group" key={group.label}>
          <h2 className="nav-heading">{group.label}</h2>
          <ul>
            {group.screens.map((screen) => (
              <li key={screen.id}>
                <NavLink to={screen.path} onClick={onNavigate}>
                  {screen.title}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function QuickNav({ onMenu, menuOpen }: { onMenu: () => void; menuOpen: boolean }) {
  const { state } = useAuth();
  if (state.status !== "authenticated") return null;
  const allowed = new Set(navigationFor(state.capabilities).flatMap((g) => g.screens.map((s) => s.id)));
  const order = [state.me.home ?? "", ...QUICK_PRIORITY].filter((id, i, all) => id && allowed.has(id) && all.indexOf(id) === i).slice(0, 3);
  const items = order.map(screenById).filter((s): s is ScreenDef => s !== undefined);
  return (
    <nav aria-label="Quick navigation" className="quick-nav">
      <ul>
        {items.map((screen) => (
          <li key={screen.id}>
            <NavLink to={screen.path}>{screen.title}</NavLink>
          </li>
        ))}
        <li>
          <button type="button" className="quick-more" aria-expanded={menuOpen} aria-controls="primary-nav" onClick={onMenu}>
            All screens
          </button>
        </li>
      </ul>
    </nav>
  );
}

/** The authenticated frame: skip link, header with feed state and the SIMULATED tag, role-filtered navigation, one `main`. */
export function AppShell({ children }: { children?: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [menuOpen]);

  return (
    <div className="shell">
      <SkipLink />
      <Header>
        <button type="button" className="menu-button btn btn-secondary" aria-expanded={menuOpen} aria-controls="primary-nav" onClick={() => setMenuOpen((v) => !v)}>
          Menu
        </button>
        <UserMenu />
      </Header>
      <Navigation open={menuOpen} onNavigate={() => setMenuOpen(false)} />
      <main id="main" tabIndex={-1} className="main">
        <ErrorBoundary resetKey={location.pathname}>{children ?? <Outlet />}</ErrorBoundary>
      </main>
      <QuickNav menuOpen={menuOpen} onMenu={() => setMenuOpen((v) => !v)} />
    </div>
  );
}

/** The signed-out frame for login, session-expired and unknown paths: the same header and landmarks, no navigation. */
export function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div className="shell shell-public">
      <SkipLink />
      <Header />
      <main id="main" tabIndex={-1} className="main">
        {children}
      </main>
    </div>
  );
}
