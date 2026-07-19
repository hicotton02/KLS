import Link from "next/link";
import { Search, Scale } from "lucide-react";

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="page-width header-row">
        <Link className="brand" href="/" aria-label="Keeping Law Simple home">
          <span className="brand-mark" aria-hidden="true"><Scale size={23} strokeWidth={1.8} /></span>
          <span className="brand-copy"><strong>Keeping Law Simple</strong><small>Legislation, clearly explained</small></span>
        </Link>
        <nav className="primary-nav" aria-label="Primary navigation">
          <Link href="/#browse">Browse states</Link>
          <Link className="nav-search" href="/search"><Search size={17} aria-hidden="true" /> Search bills</Link>
        </nav>
      </div>
    </header>
  );
}
