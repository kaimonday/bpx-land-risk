"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LINKING } from "@/lib/api";

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
        {LINKING && <Link href="/link" aria-current={current("/link")}>Link production</Link>}
      </nav>
      <span className="freshness small muted">
        {process.env.NEXT_PUBLIC_SITE_NOTE ?? "Synthetic lease documents on public Texas RRC production data"}
      </span>
    </header>
  );
}
