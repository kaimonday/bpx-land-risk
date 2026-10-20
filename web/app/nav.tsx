"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function Nav() {
  const path = usePathname();
  const current = (href: string) =>
    (href === "/" ? path === "/" || path.startsWith("/leases") : path.startsWith(href)) ? "page" : undefined;
  return (
    <header className="topbar">
      <Link href="/" className="brand">Land risk monitor</Link>
      <nav className="nav" aria-label="Main">
        <Link href="/" aria-current={current("/")}>Leases</Link>
        <Link href="/review" aria-current={current("/review")}>Review queue</Link>
      </nav>
      <span className="freshness small muted">Synthetic lease documents on public Texas RRC production data</span>
    </header>
  );
}
