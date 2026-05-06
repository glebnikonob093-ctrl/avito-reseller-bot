import React, { useEffect, useMemo, useState } from "react";

type FeedItem = {
  title: string | null;
  price: number | null;
  url: string;
  first_seen_at: string | null;
  subscription_id: number;
  external_id: string;
  source: string;
  city: string | null;
  photo_url: string | null;
  description: string | null;
  seller_profile_url: string | null;
  is_mock: boolean;
};

type Catalog = {
  id: number;
  display_name: string;
  category: string;
  region: string;
  query: string;
  price_min: number | null;
  price_max: number | null;
  is_paused: boolean;
  is_selected: boolean;
};

type Category = {
  slug: string;
  title: string;
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

  const [tab, setTab] = useState<"feed" | "catalogs">("feed");
  const [sortMode, setSortMode] = useState<"newest" | "best_deals">("newest");
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [newCatalogName, setNewCatalogName] = useState("");
  const [newCatalogCategory, setNewCatalogCategory] = useState("telefony");
  const [newCatalogRegion, setNewCatalogRegion] = useState("moskva");
  const [newCatalogQuery, setNewCatalogQuery] = useState("");
  const [items, setItems] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  async function apiFetch(path: string, init?: RequestInit) {
    const r = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        "X-Telegram-Init-Data": initData,
        ...(init?.headers || {}),
      },
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = body?.detail || `HTTP ${r.status}`;
      throw new Error(String(msg));
    }
    return body;
  }

  async function loadFeed() {
    setLoading(true);
    setError("");
    try {
      const selected = catalogs.find((c) => c.is_selected);
      const selectedPart = selected ? `&catalog_id=${selected.id}` : "";
      const body = await apiFetch(`/api/feed?limit=50&sort=${sortMode}${selectedPart}`);
      setItems(Array.isArray(body?.items) ? body.items : []);
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось загрузить ленту");
    } finally {
      setLoading(false);
    }
  }

  async function loadCatalogs() {
    try {
      const body = await apiFetch("/api/catalogs");
      setCatalogs(Array.isArray(body?.items) ? body.items : []);
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось загрузить каталоги");
    }
  }

  async function loadCategories() {
    try {
      const body = await apiFetch("/api/categories");
      const rows = Array.isArray(body?.items) ? body.items : [];
      setCategories(rows);
      if (rows.length && !rows.some((r: Category) => r.slug === newCatalogCategory)) {
        setNewCatalogCategory(rows[0].slug);
      }
    } catch {
      // optional endpoint
    }
  }

  async function createCatalog() {
    setError("");
    try {
      await apiFetch("/api/catalogs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: newCatalogName.trim() || "Мой каталог",
          category: newCatalogCategory,
          region: newCatalogRegion.trim() || "moskva",
          query: newCatalogQuery.trim(),
          select_now: true,
        }),
      });
      setNewCatalogName("");
      setNewCatalogQuery("");
      await loadCatalogs();
      await loadFeed();
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось создать каталог");
    }
  }

  async function selectCatalog(catalogId: number) {
    setError("");
    try {
      await apiFetch(`/api/catalogs/${catalogId}/select`, { method: "POST" });
      await loadCatalogs();
      await loadFeed();
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось выбрать каталог");
    }
  }

  async function deleteCatalog(catalogId: number) {
    setError("");
    try {
      await apiFetch(`/api/catalogs/${catalogId}`, { method: "DELETE" });
      await loadCatalogs();
      await loadFeed();
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось удалить каталог");
    }
  }

  useEffect(() => {
    try {
      tg?.ready();
      tg?.expand();
    } catch {
      // ignore
    }
    void loadCategories();
    void loadCatalogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void loadFeed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortMode, catalogs.find((c) => c.is_selected)?.id, catalogs.length]);

  const scheme = tg?.colorScheme ?? "dark";

  return (
    <div className="page">
      <div className="container">
        <div className="headerCard">
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
          <div className="tabs">
            <button className={`tabBtn ${tab === "feed" ? "tabBtnActive" : ""}`} onClick={() => setTab("feed")}>
              Лента
            </button>
            <button
              className={`tabBtn ${tab === "catalogs" ? "tabBtnActive" : ""}`}
              onClick={() => setTab("catalogs")}
            >
              Каталоги
            </button>
          </div>
        </div>

        {error ? (
          <div className="error">
            <div className="errorTitle">Ошибка</div>
            <div>{error}</div>
            {error.includes("User not found") ? (
              <div style={{ marginTop: 8, opacity: 0.9 }}>
                Открой бота и выполни <code>/start</code>, затем попробуй снова.
              </div>
            ) : null}
          </div>
        ) : null}

        {tab === "catalogs" ? (
          <div className="card feedCard">
            <div className="sectionTitle">Каталоги пользователя</div>
            <div className="catalogCreate">
              <input
                className="input"
                value={newCatalogName}
                placeholder="Название каталога"
                onChange={(e) => setNewCatalogName(e.target.value)}
              />
              <select
                className="input"
                value={newCatalogCategory}
                onChange={(e) => setNewCatalogCategory(e.target.value)}
              >
                {categories.map((cat) => (
                  <option key={cat.slug} value={cat.slug}>
                    {cat.title}
                  </option>
                ))}
              </select>
              <input
                className="input"
                value={newCatalogRegion}
                placeholder="Регион (slug)"
                onChange={(e) => setNewCatalogRegion(e.target.value)}
              />
              <input
                className="input"
                value={newCatalogQuery}
                placeholder="Запрос (опц.)"
                onChange={(e) => setNewCatalogQuery(e.target.value)}
              />
              <button className="btn" onClick={createCatalog}>
                Создать
              </button>
            </div>

            <div className="list">
              {catalogs.map((it) => (
                <div className="itemCard" key={it.id}>
                  <div className="itemRow">
                    <div className="itemTitle">{it.display_name || `Каталог #${it.id}`}</div>
                    <span className="badge">{it.is_selected ? "Активный" : "Обычный"}</span>
                  </div>
                  <div className="meta">
                    {it.category} · {it.region} · {it.query || "без запроса"}
                  </div>
                  <div className="catalogActions">
                    {!it.is_selected ? (
                      <button className="btn btnSmall" onClick={() => selectCatalog(it.id)}>
                        Выбрать
                      </button>
                    ) : null}
                    <button className="btn btnSmall btnDanger" onClick={() => deleteCatalog(it.id)}>
                      Удалить
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="card feedCard">
            <div className="sortRow">
              <button
                className={`chip ${sortMode === "newest" ? "chipActive" : ""}`}
                onClick={() => setSortMode("newest")}
              >
                Новые
              </button>
              <button
                className={`chip ${sortMode === "best_deals" ? "chipActive" : ""}`}
                onClick={() => setSortMode("best_deals")}
              >
                Выгодные
              </button>
            </div>
          {!items.length && !loading && !error ? (
            <div className="emptyState">
              <div className="emptyEmoji">🔎</div>
              <div className="emptyTitle">Пока пусто</div>
              <div className="emptyText">
                Дождись, пока мониторинг найдёт новые объявления по твоим подпискам.
              </div>
            </div>
          ) : null}

          {loading ? (
            <div className="skeletonList">
              <div className="skeletonCard" />
              <div className="skeletonCard" />
              <div className="skeletonCard" />
            </div>
          ) : null}

          <div className="list">
            {items.map((it) => (
              <div className="itemCard" key={`${it.source}:${it.external_id}`}>
                {it.photo_url ? <img className="photo" src={it.photo_url} alt={it.title || "item"} /> : null}
                <div className="itemTitle">{it.title || "Без названия"}</div>
                <div className="itemRow">
                  <div className="price">{formatPrice(it.price)}</div>
                  <div className="meta">{formatTime(it.first_seen_at)}</div>
                </div>
                <div className="meta">{it.city || "Город не указан"}</div>
                {it.description ? <div className="desc">{it.description}</div> : null}
                <div className="linkWrap">
                  <a className="link" href={it.url} target="_blank" rel="noreferrer">
                    Открыть объявление
                  </a>
                </div>
                <div className="meta subMeta">
                  Подписка #{it.subscription_id}
                  {it.seller_profile_url ? (
                    <>
                      {" "}
                      ·{" "}
                      <a className="link" href={it.seller_profile_url} target="_blank" rel="noreferrer">
                        Профиль продавца
                      </a>
                    </>
                  ) : null}
                  {it.is_mock ? " · demo" : ""}
                </div>
              </div>
            ))}
          </div>
        </div>
        )}
      </div>
    </div>
  );
}

