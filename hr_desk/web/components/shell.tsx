"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/components/session";

const ADMIN_LINKS = [
  { href: "/policies", label: "Policies" },
  { href: "/people", label: "People" },
  { href: "/questions", label: "Questions" },
  { href: "/checks", label: "Checks" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { loading, me, signOut } = useSession();
  const links = me ? [{ href: "/", label: "New Chat" }, ...(me.isAdmin ? ADMIN_LINKS : [])] : [];

  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="frame topbar-inner">
          <Link className="brand" href="/">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/logo.png" alt="Stylo" width={148} height={56} />
            <strong>HR Desk</strong>
          </Link>
          {me ? (
            <div className="who">
              <p>
                Signed in as <span className="who-name">{me.name}</span>,{" "}
                <span className="who-name">{me.role}</span>
              </p>
              <button type="button" onClick={() => void signOut()}>
                Sign out
              </button>
            </div>
          ) : null}
        </div>
        {links.length > 0 ? (
          <nav className="nav" aria-label="Sections">
            <div className="frame nav-inner">
              {links.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={pathname === link.href ? "page" : undefined}
                >
                  {link.label}
                </Link>
              ))}
            </div>
          </nav>
        ) : null}
      </header>
      <main id="main" className={pathname === "/" && me ? "chat-page" : "frame page"}>
        {loading ? <p role="status">Loading…</p> : children}
      </main>
    </>
  );
}
