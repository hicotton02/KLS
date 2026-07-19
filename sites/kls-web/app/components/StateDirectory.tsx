"use client";

import Link from "next/link";
import { ArrowRight, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { Jurisdiction } from "../lib/kls";

export function StateDirectory({ areas }: { areas: Jurisdiction[] }) {
  const [query, setQuery] = useState("");
  const matches = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return areas;
    return areas.filter((area) => area.name.toLowerCase().includes(normalized));
  }, [areas, query]);

  return (
    <div className="state-directory">
      <label className="state-filter">
        <span className="sr-only">Filter states</span>
        <Search size={18} aria-hidden="true" />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter states" />
      </label>
      <div className="state-grid" aria-live="polite">
        {matches.map((area) => (
          <Link className="state-link" href={`/area/${area.slug}`} key={area.slug}>
            <span>
              <strong>{area.name}</strong>
              <small>{area.counts?.total ? `${area.counts.total.toLocaleString()} bills` : "View coverage"}</small>
            </span>
            <ArrowRight size={17} aria-hidden="true" />
          </Link>
        ))}
      </div>
      {matches.length === 0 ? <p className="empty-line">No state matches “{query}”.</p> : null}
    </div>
  );
}
