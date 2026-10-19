import { useCallback, useEffect, useState } from "react";

export interface Loaded<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/** Load data for a view; reloads when `key` changes. Ignores responses that arrive late. */
export function useLoad<T>(load: () => Promise<T>, key: string): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let current = true;
    setLoading(true);
    load()
      .then((result) => {
        if (!current) return;
        setData(result);
        setError(null);
      })
      .catch((err: unknown) => {
        if (current) setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
    // `load` is recreated each render; `key` captures its inputs, so it is not a dependency.
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
