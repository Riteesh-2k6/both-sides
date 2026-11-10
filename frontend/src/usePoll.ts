import { useEffect, useRef, useState } from "react";
import { get, ApiError } from "./api";

/** GET `path` now, then every `ms` (0 = once). `until(data)` true stops polling. Pass path=null to idle. */
export function usePoll<T>(path: string | null, ms: number, enabled = true, until?: (d: T) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const untilRef = useRef(until);
  untilRef.current = until;

  useEffect(() => { setData(null); setError(null); }, [path]);

  useEffect(() => {
    if (!path || !enabled) return;
    let alive = true;
    let timer: number | undefined;
    let fails = 0;
    const load = async () => {
      let again = ms > 0;
      let wait = ms;
      try {
        const d = await get<T>(path);
        if (!alive) return;
        fails = 0; setData(d); setError(null);
        if (untilRef.current?.(d)) again = false;
      } catch (e) {
        if (!alive) return;
        setError(e as ApiError);
        if ((e as ApiError).status === 429) again = false; // stop; the caller's Retry (reload) restarts
        else wait = Math.min(Math.max(ms, 700) * 2 ** fails++, Math.max(ms, 10_000));
      }
      if (again && alive) timer = window.setTimeout(load, wait);
    };
    load();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [path, ms, enabled, tick]);

  return { data, error, reload: () => setTick((t) => t + 1) };
}
