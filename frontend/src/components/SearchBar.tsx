"use client";

import { useState } from "react";

export type SearchScope = "all" | "新資料夾" | "直播" | "其他";

interface SearchBarProps {
  initialQuery?: string;
  scope: SearchScope;
  onScopeChange: (scope: SearchScope) => void;
  onSearch: (query: string) => void;
  autoFocus?: boolean;
}

const SCOPE_BUTTONS: { value: Exclude<SearchScope, "all">; label: string }[] = [
  { value: "新資料夾", label: "只搜尋新資料夾" },
  { value: "直播", label: "只搜尋呱吉直播" },
  { value: "其他", label: "只搜尋其他內容" },
];

export default function SearchBar({
  initialQuery = "",
  scope,
  onScopeChange,
  onSearch,
  autoFocus,
}: SearchBarProps) {
  const [query, setQuery] = useState(initialQuery);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      onSearch(query.trim());
    }
  };

  const toggleScope = (value: Exclude<SearchScope, "all">) => {
    // Clicking the active scope clears it back to "all"
    onScopeChange(scope === value ? "all" : value);
  };

  return (
    <div className="nrk-search-block">
      <form onSubmit={handleSubmit} className="nrk-search">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜尋 Podcast 內容…"
          className="nrk-search__input"
          autoFocus={autoFocus}
        />
        <button type="submit" className="nrk-btn nrk-btn--primary">
          搜尋
        </button>
      </form>
      <div className="nrk-scope" role="group" aria-label="搜尋範圍">
        {SCOPE_BUTTONS.map((btn) => (
          <button
            key={btn.value}
            type="button"
            onClick={() => toggleScope(btn.value)}
            className={`nrk-scope__btn${
              scope === btn.value ? " nrk-scope__btn--active" : ""
            }`}
            aria-pressed={scope === btn.value}
          >
            {btn.label}
          </button>
        ))}
      </div>
    </div>
  );
}
