import { useEffect, useState } from "react";
import { Config, get } from "./api";
import { ConfigCtx, ErrorBox } from "./components";
import Inbox from "./pages/Inbox";
import Case from "./pages/Case";
import RunView from "./pages/RunView";
import Shop from "./pages/Shop";
import Shopper from "./pages/Shopper";
import Limits from "./pages/Limits";
import Bench from "./pages/Bench";

const TABS: { href: string; label: string; match: string }[] = [
  { href: "#/", label: "Inbox", match: "" },
  { href: "#/shop", label: "Shop", match: "shop" },
  { href: "#/shopper", label: "Shopper agent", match: "shopper" },
  { href: "#/limits", label: "Settlement limits", match: "limits" },
  { href: "#/bench", label: "Bench", match: "bench" },
];

function useHash(): string[] {
  const read = () => location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => { setParts(read()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

export default function App() {
  const parts = useHash();
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [waking, setWaking] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    const t = window.setTimeout(() => alive && setWaking(true), 3000);
    Promise.all([get("/health"), get<Config>("/config")]).then(
      ([, c]) => { if (alive) { setConfig(c); setWaking(false); } },
      (e) => { if (alive) { setError(e); setWaking(false); } },
    ).finally(() => window.clearTimeout(t));
    return () => { alive = false; window.clearTimeout(t); };
  }, [attempt]);

  const [route, arg] = parts;
  const active = route === "case" || route === "run" || !route ? "" : route;

  let page;
  if (!route) page = <Inbox />;
  else if (route === "case" && arg) page = <Case id={decodeURIComponent(arg)} />;
  else if (route === "run" && arg) page = <RunView id={arg} />;
  else if (route === "shop") page = <Shop />;
  else if (route === "shopper") page = <Shopper />;
  else if (route === "limits") page = <Limits />;
  else if (route === "bench") page = <Bench />;
  else page = <div className="page"><div className="state state-empty">That page doesn't exist. <a href="#/">Back to the Inbox</a></div></div>;

  return (
    <ConfigCtx.Provider value={config}>
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>Skip to content</a>
      <header className="topbar">
        <div className="topbar-in">
          <a className="brand" href="#/"><span className="brand-mark" aria-hidden="true">⚖</span> Both Sides</a>
          <nav aria-label="Main">
            {TABS.map((t) => (
              <a key={t.href} href={t.href} aria-current={active === t.match ? "page" : undefined}>{t.label}</a>
            ))}
          </nav>
          <div className="topbar-meta">
            <span className="chip chip-warn">Sandbox</span>
            <span className="muted small seller-hint">Seller: India · Buyers: worldwide</span>
            {config?.llm_label && <span className="llm muted small" title="Model used for hearings">{config.llm_label}</span>}
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        {error ? (
          <div className="page"><ErrorBox error={error} onRetry={() => setAttempt(attempt + 1)} /></div>
        ) : !config ? (
          <div className="page">
            <div className="state waking" role="status">
              <span className="spinner" aria-hidden="true" />
              {waking ? "Waking the server (free tier, about a minute)…" : "Connecting…"}
            </div>
          </div>
        ) : page}
      </main>
    </ConfigCtx.Provider>
  );
}
