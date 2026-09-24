import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const ClockContext = createContext<number>(Date.now());

/** One shared one-second tick, so ages ("3 s ago") stay correct without a timer per value. */
export function ClockProvider({ children }: { children: ReactNode }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return <ClockContext.Provider value={now}>{children}</ClockContext.Provider>;
}

export function useNow(): number {
  return useContext(ClockContext);
}
