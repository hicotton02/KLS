import { Search } from "lucide-react";

export function SearchBox({ defaultValue = "", compact = false }: { defaultValue?: string; compact?: boolean }) {
  return (
    <form className={compact ? "search-box search-box-compact" : "search-box"} action="/search" method="get">
      <label className="sr-only" htmlFor={compact ? "compact-search" : "home-search"}>Search bills</label>
      <Search size={21} aria-hidden="true" />
      <input
        id={compact ? "compact-search" : "home-search"}
        type="search"
        name="q"
        defaultValue={defaultValue}
        placeholder="Bill number, topic, sponsor, or plain-English phrase"
      />
      <button type="submit">Search</button>
    </form>
  );
}
