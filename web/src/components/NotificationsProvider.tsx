"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AlertSchema } from "@/lib/schemas/alert";
import Toast, { type ToastSeverity } from "./Toast";
import styles from "./NotificationsProvider.module.scss";

export type NotificationInput = {
  severity: ToastSeverity;
  title: string;
  detail?: string;
};

type Notification = NotificationInput & { id: string };

type NotificationsContextValue = { push: (n: NotificationInput) => void };

const NotificationsContext = createContext<NotificationsContextValue | null>(null);

/** Push a toast from anywhere under the provider (e.g. high-net-edge opportunity toasts). */
export function useNotifications(): NotificationsContextValue {
  const ctx = useContext(NotificationsContext);
  if (!ctx) throw new Error("useNotifications must be used within a NotificationsProvider");
  return ctx;
}

// Auto-dismiss after N ms by severity; errors are sticky (0 = manual dismiss only).
const AUTO_DISMISS_MS: Record<ToastSeverity, number> = {
  info: 6000,
  success: 6000,
  warning: 12000,
  error: 0,
};
const MAX_TOASTS = 6;

/**
 * One toast lane for everything that should interrupt the operator: system alerts streamed from
 * /api/alerts/stream (the three quant alerts) AND opportunity toasts pushed via useNotifications.
 */
export default function NotificationsProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Notification[]>([]);
  const seq = useRef(0);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: string) => {
    setItems((prev) => prev.filter((n) => n.id !== id));
    const t = timers.current.get(id);
    if (t) {
      clearTimeout(t);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (n: NotificationInput) => {
      const id = `n${seq.current++}`;
      setItems((prev) => [{ id, ...n }, ...prev].slice(0, MAX_TOASTS));
      const ttl = AUTO_DISMISS_MS[n.severity];
      if (ttl > 0) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), ttl),
        );
      }
    },
    [dismiss],
  );

  // System alerts: subscribe to the alerts SSE channel and toast each validated alert.
  useEffect(() => {
    if (typeof EventSource === "undefined") return; // SSR / test env
    const source = new EventSource("/api/alerts/stream");
    source.onmessage = (event) => {
      let json: unknown;
      try {
        json = JSON.parse(event.data);
      } catch {
        return; // ignore a malformed frame
      }
      const parsed = AlertSchema.safeParse(json);
      if (parsed.success) {
        push({
          severity: parsed.data.severity,
          title: parsed.data.title,
          detail: parsed.data.detail,
        });
      }
    };
    return () => source.close();
  }, [push]);

  // Clear any pending auto-dismiss timers on unmount.
  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const t of map.values()) clearTimeout(t);
      map.clear();
    };
  }, []);

  return (
    <NotificationsContext.Provider value={{ push }}>
      {children}
      <div className={styles.stack}>
        {items.map((n) => (
          <Toast
            key={n.id}
            severity={n.severity}
            title={n.title}
            detail={n.detail}
            onDismiss={() => dismiss(n.id)}
          />
        ))}
      </div>
    </NotificationsContext.Provider>
  );
}
