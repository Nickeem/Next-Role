import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { api, setUnauthorizedHandler } from "./api";
import { AppShell } from "./components/AppShell";
import { Empty, Loading } from "./components/States";
import { Activity } from "./pages/Activity";
import { Decisions } from "./pages/Decisions";
import { Login } from "./pages/Login";
import { ReviewDetail } from "./pages/ReviewDetail";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Settings } from "./pages/Settings";

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

  const logout = useCallback(() => {
    void api.logout().finally(() => setAuth("signed-out"));
  }, []);

  if (auth === "checking") return <Loading label="Starting" />;
  if (auth === "signed-out") return <Login onLoggedIn={() => setAuth("signed-in")} />;

  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<ReviewQueue />} />
          <Route path="/reviews/:id" element={<ReviewDetail />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/settings" element={<Settings onLogout={logout} />} />
          <Route path="*" element={<Empty title="Page not found" />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
