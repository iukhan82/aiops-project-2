import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ClockProvider } from "./components/Clock";
import { FeedProvider } from "./components/Feed";
import { SCREENS } from "./config/access";
import { CallbackPage, ForbiddenPage, HomeRedirect, LoginPage, NotFoundPage, RequireAuth, RequireCapability, SessionExpiredPage } from "./pages/access";
import { PAGES } from "./pages/registry";

const SPECIAL = new Set(["login", "forbidden", "session-expired", "not-found"]);

export function App() {
  return (
    <BrowserRouter>
      <ClockProvider>
        <AuthProvider>
          <FeedProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/callback" element={<CallbackPage />} />
              <Route path="/session-expired" element={<SessionExpiredPage />} />
              <Route element={<RequireAuth />}>
                <Route path="/" element={<HomeRedirect />} />
                <Route path="/forbidden" element={<ForbiddenPage />} />
                {SCREENS.filter((screen) => !SPECIAL.has(screen.id)).map((screen) => {
                  const Screen = PAGES[screen.id];
                  return Screen ? <Route key={screen.id} path={screen.path} element={<RequireCapability screenId={screen.id}><Screen /></RequireCapability>} /> : null;
                })}
              </Route>
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </FeedProvider>
        </AuthProvider>
      </ClockProvider>
    </BrowserRouter>
  );
}
