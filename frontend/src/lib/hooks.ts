import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';

export function useApi<T>(path: string | null, options?: { pollMs?: number }) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const currentPath = useRef(path);
  currentPath.current = path;

  const refresh = useCallback(
    async (quiet = false) => {
      if (!path) {
        setLoading(false);
        return;
      }
      controller.current?.abort();
      const request = new AbortController();
      controller.current = request;
      if (!quiet) setRefreshing(true);
      try {
        const result = await api<T>(path, { signal: request.signal });
        if (currentPath.current === path && !request.signal.aborted) {
          setData(result);
          setError(null);
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        if (!request.signal.aborted) setError(errorMessage(e));
      } finally {
        if (!request.signal.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [path],
  );

  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(true);
    void refresh();
    return () => controller.current?.abort();
  }, [refresh]);

  useEffect(() => {
    if (!options?.pollMs || !path) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh(true);
    }, options.pollMs);
    return () => window.clearInterval(id);
  }, [options?.pollMs, path, refresh]);

  return { data, error, loading, refreshing, refresh, setData };
}

export function useAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  const run = async <T>(
    action: () => Promise<T>,
    onSuccess?: (data: T) => void | Promise<void>,
  ) => {
    if (lock.current) return;
    lock.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await action();
      await onSuccess?.(result);
      return result;
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      lock.current = false;
      setPending(false);
    }
  };
  return { pending, error, run, clearError: () => setError(null) };
}
