import React, { useEffect, useMemo, useState } from "react";

type FeedItem = {
  title: string | null;
  price: number | null;
  url: string;
  first_seen_at: string | null;
  subscription_id: number;
  external_id: string;
  source: string;
};

function formatPrice(p: number | null) {
  if (p === null || Number.isNaN(p)) return "—";
  return `${p.toLocaleString("ru-RU")} ₽`;
}

function formatTime(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("ru-RU");
}

export function App() {
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData ?? "";

  const apiBase = useMemo(() => {
    // By default, API is served from bot process at http://127.0.0.1:8000
    // For tunnel/prod you can serve frontend from same origin as API.
    const fromEnv = (import.meta as any).env?.VITE_API_BASE as string | undefined;
    return (fromEnv && fromEnv.trim()) || "http://127.0.0.1:8000";
  }, []);

  const [items, setItems] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  async function loadFeed() {
    setLoading(true);
    setError("");
    try {
      const r = await fetch(`${apiBase}/api/feed?limit=50`, {
        headers: {
          "X-Telegram-Init-Data": initData,
        },
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const msg = body?.detail || `HTTP ${r.status}`;
        throw new Error(String(msg));
      }
      setItems(Array.isArray(body?.items) ? body.items : []);
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось загрузить ленту");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    try {
      tg?.ready();
      tg?.expand();
    } catch {
      // ignore
    }
    loadFeed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scheme = tg?.colorScheme ?? "dark";

  return (
    <div className="container">
      <div className="header">
        <div>
          <div className="title">Лента новых объявлений</div>
          <div className="meta">
            {items.length ? (
              <>
                <span className="badge">{items.length} шт.</span> ·{" "}
              </>
            ) : null}
            {scheme === "dark" ? "Тёмная" : "Светлая"} тема
          </div>
        </div>
        <button className="btn" onClick={loadFeed} disabled={loading}>
          {loading ? "Обновляю…" : "Обновить"}
        </button>
      </div>

      {error ? (
        <div className="error">
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Ошибка</div>
          <div>{error}</div>
          {error.includes("User not found") ? (
            <div style={{ marginTop: 8, opacity: 0.9 }}>
              Открой бота и выполни <code>/start</code>, затем попробуй снова.
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="card" style={{ marginTop: 12 }}>
        {!items.length && !loading && !error ? (
          <div style={{ opacity: 0.85 }}>
            Пока пусто. Дождись, пока мониторинг найдёт новые объявления по твоим подпискам.
          </div>
        ) : null}

        <div className="list">
          {items.map((it) => (
            <div className="card" key={`${it.source}:${it.external_id}`}>
              <div className="itemTitle">{it.title || "Без названия"}</div>
              <div className="itemRow">
                <div className="price">{formatPrice(it.price)}</div>
                <div className="meta">{formatTime(it.first_seen_at)}</div>
              </div>
              <div style={{ marginTop: 8 }}>
                <a className="link" href={it.url} target="_blank" rel="noreferrer">
                  {it.url}
                </a>
              </div>
              <div className="meta" style={{ marginTop: 8 }}>
                Подписка #{it.subscription_id}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

