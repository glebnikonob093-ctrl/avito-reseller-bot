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
  deal_score: number;
  work_status: "new" | "contacted" | "negotiating" | "bought" | "sold";
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

type City = {
  slug: string;
  title: string;
};

type UserProfile = {
  id: number;
  tg_user_id: number;
  first_name: string;
  username: string;
  role: "user" | "admin";
  subscription_tier: "free" | "pro";
  account_status: "Free" | "Pro" | "Admin";
  is_admin: boolean;
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
  const [initData, setInitData] = useState("");

  const apiBase = useMemo(() => {
    // Priority: explicit env -> local dev API -> same-origin production API.
    const fromEnv = (import.meta as any).env?.VITE_API_BASE as string | undefined;
    const normalizedEnv = fromEnv?.trim().replace(/\/+$/, "");
    if (normalizedEnv) return normalizedEnv;

    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1") {
      return "http://127.0.0.1:8000";
    }
    return window.location.origin.replace(/\/+$/, "");
  }, []);

  const [tab, setTab] = useState<"feed" | "catalogs" | "notifications" | "profile">("feed");
  const [sortMode, setSortMode] = useState<"newest" | "best_deals">("newest");
  const [minDealScore, setMinDealScore] = useState<number>(0);
  const [maxPriceFilter, setMaxPriceFilter] = useState<string>("");
  const [onlyWithPhoto, setOnlyWithPhoto] = useState(false);
  const [workStatusFilter, setWorkStatusFilter] = useState<string>("");
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [cities, setCities] = useState<City[]>([]);
  const [citiesLoading, setCitiesLoading] = useState(false);
  const [citiesError, setCitiesError] = useState("");
  const [citySearchOpen, setCitySearchOpen] = useState(false);
  const [newCatalogName, setNewCatalogName] = useState("");
  const [newCatalogCategory, setNewCatalogCategory] = useState("");
  const [newCatalogRegion, setNewCatalogRegion] = useState("");
  const [newCatalogQuery, setNewCatalogQuery] = useState("");
  const [items, setItems] = useState<FeedItem[]>([]);
  const [notifications, setNotifications] = useState<FeedItem[]>([]);
  const [userRole, setUserRole] = useState<"user" | "admin">("user");
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingLive, setLoadingLive] = useState(false);
  const [error, setError] = useState<string>("");
  const [runtimeError, setRuntimeError] = useState("");
  const isApiReady = initData.trim().length > 0;

  async function apiFetch(path: string, init?: RequestInit) {
    let r: Response;
    try {
      r = await fetch(`${apiBase}${path}`, {
        ...init,
        headers: {
          "X-Telegram-Init-Data": initData,
          ...(init?.headers || {}),
        },
      });
    } catch {
      throw new Error(`Load failed: нет соединения с API (${apiBase})`);
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = body?.detail || `HTTP ${r.status}`;
      throw new Error(String(msg));
    }
    return body;
  }

  async function loadFeed() {
    if (!isApiReady) return;
    setLoading(true);
    setError("");
    try {
      const selected = catalogs.find((c) => c.is_selected);
      const selectedPart = selected ? `&catalog_id=${selected.id}` : "";
      const qs = new URLSearchParams();
      qs.set("limit", "50");
      qs.set("sort", sortMode);
      if (selected) qs.set("catalog_id", String(selected.id));
      if (minDealScore > 0) qs.set("min_deal_score", String(minDealScore));
      if (maxPriceFilter.trim()) qs.set("max_price", maxPriceFilter.trim());
      if (onlyWithPhoto) qs.set("only_with_photo", "true");
      if (workStatusFilter) qs.set("work_status", workStatusFilter);
      const body = await apiFetch(`/api/feed?${qs.toString()}`);
      setItems(Array.isArray(body?.items) ? body.items : []);
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось загрузить ленту");
    } finally {
      setLoading(false);
    }
  }

  async function loadLiveFeed() {
    if (!isApiReady) return;
    setLoadingLive(true);
    setError("");
    try {
      const selected = catalogs.find((c) => c.is_selected);
      const qs = new URLSearchParams();
      qs.set("limit", "5");
      if (selected) qs.set("catalog_id", String(selected.id));
      const body = await apiFetch(`/api/feed/live?${qs.toString()}`);
      setItems(Array.isArray(body?.items) ? body.items : []);
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось загрузить live-объявления Avito");
    } finally {
      setLoadingLive(false);
    }
  }

  async function loadCatalogs() {
    if (!isApiReady) return;
    try {
      const body = await apiFetch("/api/catalogs");
      setCatalogs(Array.isArray(body?.items) ? body.items : []);
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось загрузить каталоги");
    }
  }

  async function loadNotifications() {
    if (!isApiReady) return;
    setError("");
    try {
      const body = await apiFetch("/api/notifications?limit=20");
      setNotifications(Array.isArray(body?.items) ? body.items : []);
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось загрузить уведомления");
    }
  }

  async function loadMe() {
    if (!isApiReady) return;
    try {
      const body = await apiFetch("/api/me");
      setProfile(body as UserProfile);
      if (body?.role === "admin") {
        setUserRole("admin");
      } else {
        setUserRole("user");
      }
    } catch {
      // optional
    }
  }

  async function loadCategories() {
    if (!isApiReady) return;
    try {
      const body = await apiFetch("/api/categories");
      const rows = Array.isArray(body?.items) ? body.items : [];
      setCategories(rows);
    } catch {
      // optional endpoint
    }
  }

  async function loadCities(query: string) {
    if (!isApiReady) return;
    const normalized = query.trim();
    setCitiesError("");
    if (normalized.length < 2) {
      setCities([]);
      return;
    }
    const q = encodeURIComponent(normalized);
    setCitiesLoading(true);
    try {
      const body = await apiFetch(`/api/cities?q=${q}&limit=25`);
      const rows = Array.isArray(body?.items) ? body.items : [];
      setCities(rows);
    } catch {
      setCities([]);
      setCitiesError("Сервис поиска населённых пунктов недоступен");
    } finally {
      setCitiesLoading(false);
    }
  }

  async function createCatalog() {
    if (!isApiReady) return;
    setError("");
    const category = newCatalogCategory.trim();
    const region = newCatalogRegion.trim();
    if (!category) {
      setError("Выберите категорию");
      return;
    }
    if (!region) {
      setError("Выберите город/населённый пункт");
      return;
    }
    try {
      await apiFetch("/api/catalogs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: newCatalogName.trim() || "Мой каталог",
          category,
          region,
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
    if (!isApiReady) return;
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
    if (!isApiReady) return;
    setError("");
    try {
      await apiFetch(`/api/catalogs/${catalogId}`, { method: "DELETE" });
      await loadCatalogs();
      await loadFeed();
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось удалить каталог");
    }
  }

  async function updateWorkStatus(item: FeedItem, status: FeedItem["work_status"]) {
    if (!isApiReady) return;
    try {
      await apiFetch("/api/work-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: item.source,
          external_id: item.external_id,
          status,
        }),
      });
      setItems((prev) =>
        prev.map((x) =>
          x.source === item.source && x.external_id === item.external_id ? { ...x, work_status: status } : x,
        ),
      );
    } catch (e: any) {
      setError(e?.message ? String(e.message) : "Не удалось обновить статус");
    }
  }

  useEffect(() => {
    const onError = (e: ErrorEvent) => {
      setRuntimeError(e.message || "Runtime error");
    };
    window.addEventListener("error", onError);
    return () => window.removeEventListener("error", onError);
  }, []);

  useEffect(() => {
    try {
      tg?.ready();
      tg?.expand();
    } catch {
      // ignore
    }
    const syncInitData = () => {
      const v = (window.Telegram?.WebApp?.initData || "").trim();
      if (v) {
        setInitData(v);
        return true;
      }
      return false;
    };
    if (!syncInitData()) {
      const timer = window.setInterval(() => {
        if (syncInitData()) {
          window.clearInterval(timer);
        }
      }, 250);
      window.setTimeout(() => window.clearInterval(timer), 5000);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isApiReady) return;
    setError("");
    void loadCategories();
    void loadCatalogs();
    void loadMe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isApiReady]);

  useEffect(() => {
    if (!isApiReady) return;
    const t = window.setTimeout(() => {
      void loadCities(newCatalogRegion);
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newCatalogRegion]);

  useEffect(() => {
    if (!isApiReady) return;
    void loadFeed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortMode, catalogs.find((c) => c.is_selected)?.id, catalogs.length, minDealScore, maxPriceFilter, onlyWithPhoto, workStatusFilter]);

  useEffect(() => {
    if (!isApiReady) return;
    if (tab === "notifications") {
      void loadNotifications();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const canCreateCatalog = Boolean(newCatalogCategory.trim() && newCatalogRegion.trim());

  const scheme = tg?.colorScheme ?? "dark";

  const selectedCatalog = catalogs.find((c) => c.is_selected);

  function renderFeed() {
    return (
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
          <button className="chip" onClick={() => void loadLiveFeed()} disabled={loadingLive}>
            {loadingLive ? "Live..." : "Тест Avito (5)"}
          </button>
        </div>
        <div className="sortRow" style={{ marginTop: 8, flexWrap: "wrap" }}>
          <label className="meta">Deal score от</label>
          <input
            className="input"
            style={{ maxWidth: 90 }}
            type="number"
            min={0}
            max={100}
            value={minDealScore}
            onChange={(e) => setMinDealScore(Math.max(0, Math.min(100, Number(e.target.value || 0))))}
          />
          <input
            className="input"
            style={{ maxWidth: 150 }}
            type="number"
            min={0}
            placeholder="Цена до"
            value={maxPriceFilter}
            onChange={(e) => setMaxPriceFilter(e.target.value)}
          />
          <label className="meta">
            <input type="checkbox" checked={onlyWithPhoto} onChange={(e) => setOnlyWithPhoto(e.target.checked)} /> Только с фото
          </label>
          <select className="input" style={{ maxWidth: 170 }} value={workStatusFilter} onChange={(e) => setWorkStatusFilter(e.target.value)}>
            <option value="">Все статусы</option>
            <option value="new">Новые</option>
            <option value="contacted">Написал</option>
            <option value="negotiating">Торг</option>
            <option value="bought">Купил</option>
            <option value="sold">Продал</option>
          </select>
        </div>

        {!items.length && !loading && !error ? (
          <div className="emptyState">
            <div className="emptyEmoji">🔎</div>
            <div className="emptyTitle">Пока пусто</div>
            <div className="emptyText">Дождись, пока мониторинг найдёт новые объявления по твоим подпискам.</div>
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
              <div className="meta">Deal score: {Math.round(it.deal_score || 0)}</div>
              <div className="meta">{it.city || "Город не указан"}</div>
              {it.description ? <div className="desc">{it.description}</div> : null}
              <div className="linkWrap">
                <a className="link" href={it.url} target="_blank" rel="noreferrer">
                  Открыть объявление
                </a>
              </div>
              <div className="meta subMeta">
                Каталог #{it.subscription_id}
                {it.seller_profile_url ? (
                  <>
                    {" "}
                    ·{" "}
                    <a className="link" href={it.seller_profile_url} target="_blank" rel="noreferrer">
                      Профиль продавца
                    </a>
                  </>
                ) : null}
                {it.is_mock ? " · тестовые данные" : ""}
              </div>
              <div className="catalogActions">
                <select
                  className="input"
                  value={it.work_status || "new"}
                  onChange={(e) => void updateWorkStatus(it, e.target.value as FeedItem["work_status"])}
                >
                  <option value="new">Новый</option>
                  <option value="contacted">Написал</option>
                  <option value="negotiating">Торг</option>
                  <option value="bought">Купил</option>
                  <option value="sold">Продал</option>
                </select>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  function renderCatalogs() {
    return (
      <div className="card feedCard">
        <div className="sectionTitle">Каталоги пользователя</div>
        <div className="catalogCreate">
          <input
            className="input"
            value={newCatalogName}
            placeholder="Название каталога"
            onChange={(e) => setNewCatalogName(e.target.value)}
          />
          <select className="input" value={newCatalogCategory} onChange={(e) => setNewCatalogCategory(e.target.value)}>
            <option value="">Выберите категорию</option>
            {categories.map((cat) => (
              <option key={cat.slug} value={cat.slug}>
                {cat.title}
              </option>
            ))}
          </select>
          <div className="citySelectWrap">
            <input
              className="input"
              value={newCatalogRegion}
              placeholder="Выберите город"
              onChange={(e) => {
                setNewCatalogRegion(e.target.value);
                setCitySearchOpen(true);
              }}
              onFocus={() => setCitySearchOpen(true)}
            />
            {citySearchOpen ? (
              <div className="cityDropdown">
                {cities.slice(0, 12).map((city) => (
                  <button
                    key={city.slug}
                    type="button"
                    className="cityOption"
                    onClick={() => {
                      setNewCatalogRegion(city.title);
                      setCitySearchOpen(false);
                    }}
                  >
                    {city.title}
                  </button>
                ))}
                {citiesLoading ? <div className="cityEmpty">Ищу населённые пункты...</div> : null}
                {!citiesLoading && newCatalogRegion.trim().length < 2 ? (
                  <div className="cityEmpty">Введите минимум 2 символа</div>
                ) : null}
                {!citiesLoading && citiesError ? <div className="cityEmpty">{citiesError}</div> : null}
                {!citiesLoading && newCatalogRegion.trim().length >= 2 && !cities.length ? (
                  <div className="cityEmpty">Населённый пункт не найден</div>
                ) : null}
              </div>
            ) : null}
          </div>
          <input
            className="input"
            value={newCatalogQuery}
            placeholder="Запрос (опц.)"
            onChange={(e) => setNewCatalogQuery(e.target.value)}
          />
          <button className="btn" onClick={createCatalog} disabled={!canCreateCatalog}>
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
    );
  }

  function renderNotifications() {
    return (
      <div className="card feedCard">
        <div className="sectionTitle">Последние уведомления</div>
        {!notifications.length && !error ? (
          <div className="emptyState">
            <div className="emptyEmoji">🔔</div>
            <div className="emptyTitle">Уведомлений пока нет</div>
            <div className="emptyText">Как только появятся новые позиции, они будут здесь.</div>
          </div>
        ) : null}
        <div className="list">
          {notifications.map((it) => (
            <div className="itemCard" key={`n-${it.source}:${it.external_id}`}>
              <div className="itemTitle">{it.title || "Без названия"}</div>
              <div className="itemRow">
                <div className="price">{formatPrice(it.price)}</div>
                <div className="meta">{formatTime(it.first_seen_at)}</div>
              </div>
              <div className="meta">{it.city || "Город не указан"}</div>
              <div className="linkWrap">
                <a className="link" href={it.url} target="_blank" rel="noreferrer">
                  Открыть объявление
                </a>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  function renderProfile() {
    return (
      <div className="card feedCard">
        <div className="sectionTitle">Профиль пользователя</div>
        {profile ? (
          <div className="profileCard">
            <div className="profileRow">
              <span className="meta">Имя</span>
              <strong>{profile.first_name || "—"}</strong>
            </div>
            <div className="profileRow">
              <span className="meta">Username</span>
              <strong>{profile.username ? `@${profile.username}` : "не указан"}</strong>
            </div>
            <div className="profileRow">
              <span className="meta">Telegram ID</span>
              <strong>{profile.tg_user_id}</strong>
            </div>
            <div className="profileRow">
              <span className="meta">Роль</span>
              <strong>{profile.is_admin ? "Администратор" : "Пользователь"}</strong>
            </div>
            <div className="profileRow">
              <span className="meta">Статус</span>
              <strong>{profile.account_status || "Free"}</strong>
            </div>
          </div>
        ) : (
          <div className="emptyState">
            <div className="emptyEmoji">👤</div>
            <div className="emptyTitle">Профиль загружается</div>
          </div>
        )}
      </div>
    );
  }

  let content = renderFeed();
  if (tab === "catalogs") content = renderCatalogs();
  if (tab === "notifications") content = renderNotifications();
  if (tab === "profile") content = renderProfile();

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
                {userRole === "admin" ? " · админ" : ""}
                {selectedCatalog ? ` · ${selectedCatalog.display_name || "активный каталог"}` : ""}
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
            <button
              className={`tabBtn ${tab === "notifications" ? "tabBtnActive" : ""}`}
              onClick={() => setTab("notifications")}
            >
              Уведомления
            </button>
            <button
              className={`tabBtn ${tab === "profile" ? "tabBtnActive" : ""}`}
              onClick={() => setTab("profile")}
            >
              Профиль
            </button>
          </div>
        </div>

        {runtimeError ? (
          <div className="error">
            <div className="errorTitle">Ошибка интерфейса</div>
            <div>{runtimeError}</div>
          </div>
        ) : null}

        {!isApiReady ? (
          <div className="error">
            <div className="errorTitle">Подключение к Telegram</div>
            <div>Инициализирую Mini App...</div>
          </div>
        ) : null}

        {error ? (
          <div className="error">
            <div className="errorTitle">Ошибка</div>
            <div>{error}</div>
            {error.includes("Пользователь не найден") ? (
              <div style={{ marginTop: 8, opacity: 0.9 }}>
                Нажми /start в боте и повтори попытку.
              </div>
            ) : null}
          </div>
        ) : null}

        {content}
      </div>
    </div>
  );
}

