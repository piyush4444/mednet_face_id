/**
 * useFacility — the active-facility context behind the topbar switcher
 * (the multi-facility analog of the screenshot's "Cost Center").
 *
 * Fetches the facility list once; the selection persists in localStorage and
 * scopes facility-aware views. Degrades gracefully: if the account can't list
 * facilities (no facilities.manage under auth) the list is empty and the
 * switcher hides — the app still works against a single implicit facility.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";
import { listFacilities } from "../api/facilities";

const FacilityContext = createContext(null);
const LS_KEY = "iris-facility-id";

export function FacilityProvider({ children }) {
  const [facilities, setFacilities] = useState([]);
  const [facilityId, setId] = useState(() => {
    const v = localStorage.getItem(LS_KEY);
    return v ? Number(v) : null;
  });

  useEffect(() => {
    (async () => {
      try {
        const res = await listFacilities();
        const rows = res.facilities || [];
        setFacilities(rows);
        setId((cur) =>
          cur && rows.some((f) => f.id === cur) ? cur : rows[0]?.id ?? null,
        );
      } catch {
        setFacilities([]); // not permitted / none configured — switcher hides
      }
    })();
  }, []);

  const setFacilityId = useCallback((id) => {
    const n = id ? Number(id) : null;
    setId(n);
    if (n) localStorage.setItem(LS_KEY, String(n));
    else localStorage.removeItem(LS_KEY);
  }, []);

  const value = useMemo(
    () => ({
      facilities,
      facilityId,
      facility: facilities.find((f) => f.id === facilityId) || null,
      setFacilityId,
    }),
    [facilities, facilityId, setFacilityId],
  );

  return (
    <FacilityContext.Provider value={value}>
      {children}
    </FacilityContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFacility() {
  return (
    useContext(FacilityContext) || {
      facilities: [],
      facilityId: null,
      facility: null,
      setFacilityId: () => {},
    }
  );
}
