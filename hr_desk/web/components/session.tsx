"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api, type Person } from "@/lib/api";

type SessionValue = {
  loading: boolean;
  localPasswordSetup: boolean;
  me: Person | null;
  farewell: string;
  loadError: string;
  retry: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  setInitialPassword: (email: string, password: string) => Promise<void>;
  activate: (token: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [localPasswordSetup, setLocalPasswordSetup] = useState(false);
  const [me, setMe] = useState<Person | null>(null);
  const [farewell, setFarewell] = useState("");
  const [loadError, setLoadError] = useState("");

  async function refresh() {
    const data = await api<{ me: Person | null; localPasswordSetup: boolean }>("/api/bootstrap");
    setMe(data.me);
    setLocalPasswordSetup(data.localPasswordSetup);
    setLoadError("");
  }

  useEffect(() => {
    refresh()
      .catch((caught) => {
        setMe(null);
        setLoadError(caught instanceof Error ? caught.message : "The page didn't load. Try again.");
      })
      .finally(() => setLoading(false));
  }, []);

  async function signIn(email: string, password: string) {
    const data = await api<{ me: Person }>("/api/session", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setFarewell("");
    setMe(data.me);
  }

  async function activate(token: string, password: string) {
    const data = await api<{ me: Person }>("/api/activate", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    });
    setFarewell("");
    setMe(data.me);
  }

  async function setInitialPassword(email: string, password: string) {
    const data = await api<{ me: Person }>("/api/sign-up", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setFarewell("");
    setMe(data.me);
  }

  async function signOut() {
    const first = (me?.name || "").trim().split(/\s+/)[0] || "";
    await api("/api/sign-out", { method: "POST" });
    setMe(null);
    setFarewell(first);
  }

  return (
    <SessionContext.Provider value={{ loading, localPasswordSetup, me, farewell, loadError, retry: refresh, signIn, setInitialPassword, activate, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("Session is only available inside the page shell.");
  }
  return value;
}
