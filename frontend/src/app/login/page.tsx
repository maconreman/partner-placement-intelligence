"use client";

import { useState } from "react";
import { BrandMark, ThemeToggle } from "@/components/Brand";

// The access gate. A correct username/password sets a signed session cookie on
// the backend, after which the user is redirected into the app. This replaces
// the old browser Basic Auth popup with a styled, branded sign-in.
export default function LoginGate() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!username.trim() || !password) {
      setError("Enter your username and password");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const resp = await fetch("/api/gate/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      if (resp.ok) {
        window.location.href = "/";
        return;
      }
      const j = await resp.json().catch(() => ({}));
      setError(j.error || "Sign in failed, try again");
    } catch {
      setError("Could not reach the server, try again");
    } finally {
      setSubmitting(false);
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") submit();
  };

  return (
    <div className="gate-wrap">
      <div className="gate-card">
        <div className="gate-toggle-row">
          <ThemeToggle />
        </div>
        <div className="gate-brand">
          <BrandMark />
          <div>
            <div className="brand-name">Nexus Placement Intelligence</div>
            <div className="brand-sub">FFG Universe</div>
          </div>
        </div>

        <h1>Sign in</h1>
        <p className="gate-lead">Enter your credentials to open the tool</p>

        <label className="field">
          <span>Username</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={onKey}
            autoFocus
            autoComplete="username"
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={onKey}
            autoComplete="current-password"
          />
        </label>

        {error && <div className="notice error" style={{ marginTop: 8 }}>{error}</div>}

        <div className="gate-actions">
          <button className="primary" onClick={submit} disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </div>
    </div>
  );
}
