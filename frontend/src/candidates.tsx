import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import type { CandidateSummary } from "./types";

const STORAGE_KEY = "next-role.candidate";

export interface CandidateScope {
  candidates: CandidateSummary[];
  /** The candidate every list is scoped to; null only when there are no candidates. */
  selected: CandidateSummary | null;
  select: (id: string) => void;
}

const CandidateContext = createContext<CandidateScope>({
  candidates: [],
  selected: null,
  select: () => {},
});

function stored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Scopes the workspace to one candidate, so reviews of the same job never mix. */
export function CandidateProvider({
  candidates,
  children,
}: {
  candidates: CandidateSummary[];
  children: ReactNode;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(stored);
  const select = useCallback((id: string) => {
    setSelectedId(id);
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Remembering the choice is a convenience only.
    }
  }, []);
  const value = useMemo(
    () => ({
      candidates,
      selected: candidates.find((c) => c.id === selectedId) ?? candidates[0] ?? null,
      select,
    }),
    [candidates, selectedId, select],
  );
  return <CandidateContext.Provider value={value}>{children}</CandidateContext.Provider>;
}

export function useCandidate(): CandidateScope {
  return useContext(CandidateContext);
}

/** The `candidate_id` query value for scoped requests. */
export function useCandidateId(): string | undefined {
  return useCandidate().selected?.id;
}
