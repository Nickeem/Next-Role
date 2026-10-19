import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { api, setUnauthorizedHandler } from "./api";
import { AppShell } from "./components/AppShell";
import { CandidateProvider } from "./candidates";
import { Empty, ErrorState, Loading } from "./components/States";
import { Activity } from "./pages/Activity";
import { Decisions } from "./pages/Decisions";
import { Login } from "./pages/Login";
import { ReviewDetail } from "./pages/ReviewDetail";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Settings } from "./pages/Settings";
import { useLoad } from "./useLoad";

type AuthState = "checking" | "signed-in" | "signed-out";

export function App() {
  const [auth, setAuth] = useState<AuthState>("checking");

  const check = useCallback(() => {
    api
      .session()
      .then((s) => setAuth(s.authenticated ? "signed-in" : "signed-out"))
      .catch(() => setAuth("signed-out"));
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setAuth("signed-out"));
    check();
  }, [check]);

  // Only leave the workspace once the server has revoked the session. On failure the
  // cookie is still valid, so the error propagates and the user stays signed in.
  const logout = useCallback(async () => {
    await api.logout();
    setAuth("signed-out");
  }, []);

  if (auth === "checking") return <Loading label="Starting" />;
  if (auth === "signed-out") return <Login onLoggedIn={() => setAuth("signed-in")} />;

  return <Workspace onLogout={logout} />;
}

function Workspace({ onLogout }: { onLogout: () => Promise<void> }) {
  const candidates = useLoad(() => api.candidates(), "candidates");
  if (candidates.loading && !candidates.data) return <Loading label="Starting" />;
  if (!candidates.data) {
    return <ErrorState error={candidates.error ?? new Error("No data")} onRetry={candidates.reload} />;
  }

  return (
    <CandidateProvider candidates={candidates.data}>
      <BrowserRouter>
        <AppShell>
        <Routes>
          <Route path="/" element={<ReviewQueue />} />
          <Route path="/reviews/:id" element={<ReviewDetail />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/settings" element={<Settings onLogout={onLogout} />} />
          <Route path="*" element={<Empty title="Page not found" />} />
        </Routes>
        </AppShell>
      </BrowserRouter>
    </CandidateProvider>
  );
}
