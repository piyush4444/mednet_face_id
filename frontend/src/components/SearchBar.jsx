import { useState } from "react";
import { API_URL as API } from "../config";

export default function SearchBar({ onSelect }) {
  const [query, setQuery] = useState("");

  const handleSearch = async () => {
    if (!query) return;

    try {
      const res = await fetch(
        `${API}/tracking/find?query=${encodeURIComponent(query)}`,
        { headers: { "ngrok-skip-browser-warning": "true" } }
      );
      const data = await res.json();

      if (data.patient) {
        onSelect({
          ...data.patient,
          location: data.location,
          status: data.status,
          last_seen_at: data.last_seen_at || null,
        });
      } else {
        onSelect(null);
      }
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="w-full max-w-md">
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="Search by MRN or name…"
          className="flex-1 px-4 py-2 rounded-lg bg-card border border-gray-300 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-primary"
        />
        <button
          onClick={handleSearch}
          className="px-4 py-2 bg-primary text-white rounded-lg font-semibold hover:bg-primary-dark cursor-pointer"
        >
          Find
        </button>
      </div>
      <p className="text-xs text-text-light mt-1">Search by MRN or Name</p>
    </div>
  );
}
