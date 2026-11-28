"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/components/session";

const ADMIN_LINKS = [
  { href: "/policies", label: "Policies" },
  { href: "/people", label: "People" },
  { href: "/logs", label: "Logs" },
  { href: "/checks", label: "Checks" },
];

function ProfileIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <circle cx="12" cy="8" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M5 19.2c1.4-3 3.8-4.4 7-4.4s5.6 1.4 7 4.4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { loading, me, signOut } = useSession();
  const links = me?.isAdmin ? [{ href: "/", label: "New Chat" }, ...ADMIN_LINKS] : [];

  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="frame topbar-inner">
          <Link className="brand" href="/">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/logo.png" alt="Stylo" width={100} height={40} />
          </Link>
          {links.length > 0 ? (
            <nav className="nav" aria-label="Sections">
              {links.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={pathname === link.href ? "page" : undefined}
                >
                  {link.label}
                </Link>
              ))}
            </nav>
          ) : null}
          {me ? (
            <div className="who">
              <button type="button" onClick={() => void signOut()}>
                Sign out
              </button>
              <div className="profile">
                <button type="button" className="profile-button" aria-label={`Signed in as ${me.name}`}>
                  <ProfileIcon />
                </button>
                <div className="profile-card">
                  <strong>{me.name}</strong>
                  <span>
                    {me.role}
                    {me.place ? ` · ${me.place}` : ""}
                  </span>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </header>
      <main id="main" className={pathname === "/" && me ? "chat-page" : "frame page"}>
        {loading ? <p role="status">Loading…</p> : children}
      </main>
    </>
  );
}
