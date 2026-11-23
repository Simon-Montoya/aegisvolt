import { useCallback, useEffect, useMemo, useState } from "react";
import {
  DASHBOARD_QUERY, ENDPOINTS, FINISH_MUTATION, RESERVE_MUTATION, RETRY_MUTATION, SET_FLAG_MUTATION,
  TELEMETRY_QUERY, WALLET_QUERY, gql,
  type Connector, type Dashboard, type Env, type Saga, type Telemetry, type Wallet,
} from "./api";

const ENVS: Env[] = ["dev", "test", "prod"];
const FLEETS = [
  { id: "FLEET-RAPPI-EV", label: "Reparto Urbano Norte" },
  { id: "FLEET-SERVI-EV", label: "ServiExpress Última Milla" },
  { id: "FLEET-LOWBAL", label: "Flota Demo Saldo Bajo" },
];
const STATUS_TEXT: Record<Connector["status"], string> = {
  AVAILABLE: "Disponible", RESERVED: "Reservado", CHARGING: "Cargando", OUT_OF_SERVICE: "Fuera de servicio",
};
const FLAG_TEXT: Record<string, string> = {
  ENABLE_DYNAMIC_SURGE_PRICING: "Tarifa dinámica en horas pico",
  ENABLE_FAST_CHARGE_BOOST: "Reserva prioritaria de ultra-carga",
  SIMULATE_CHARGER_HARDWARE_FAULT: "Simular falla física del cargador",
};
const SAGA_TEXT: Record<string, string> = {
  COMPLETED: "Carga activa", COMPENSATED: "Revertida", REJECTED: "Rechazada",
  COMPENSATION_FAILED: "Reversión pendiente", COMPENSATING: "Revirtiendo", STARTED: "En curso", RESERVED: "En curso", CHARGED: "En curso",
};
const cop = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
const time = (iso: string) => new Date(iso).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

function usePolling(fn: () => void, ms: number, deps: unknown[]) {
  useEffect(() => {
    fn();
    const id = window.setInterval(fn, ms);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export default function App() {
  const [env, setEnv] = useState<Env>("dev");
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const refresh = useCallback(() => {
    gql<Dashboard>(env, DASHBOARD_QUERY)
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message));
  }, [env]);

  usePolling(refresh, 2000, [refresh]);
  useEffect(() => { setData(null); setSelected(null); }, [env]);

  const connectors = useMemo(() => data?.stations.flatMap((s) => s.connectors.map((c) => ({ ...c, station: s.name }))) ?? [], [data]);
  const selectedConnector = connectors.find((c) => c.connectorId === selected) ?? null;

  return (
    <div className={`app env-${env}`}>
      <header className="band">
        <div className="band-inner">
          <div className="brand">
            <span className="bolt" aria-hidden="true" />
            <span className="brand-name">AegisVolt</span>
          </div>
          <nav className="env-switch" aria-label="Ambiente">
            {ENVS.map((e) => (
              <button key={e} className={e === env ? "on" : ""} aria-pressed={e === env} onClick={() => setEnv(e)}>{e}</button>
            ))}
          </nav>
          <EnvStatus data={data} error={error} env={env} />
        </div>
      </header>

      {error && !data ? (
        <main className="offline">
          <h1>No hay conexión con el ambiente {env}</h1>
          <p>El gateway en <code>{ENDPOINTS[env]}</code> no responde ({error}).</p>
          <p>Inicie el port-forward del ambiente con <code>scripts/port_forward.ps1 {env}</code> o levante la app con Docker Compose.</p>
        </main>
      ) : (
        <main className="grid">
          <section className="bays" aria-labelledby="bays-title">
            <h2 id="bays-title">Bahías de carga</h2>
            {data?.stations.map((s) => (
              <div className="station" key={s.stationId}>
                <div className="station-head">
                  <h3>{s.name}</h3>
                  <span className="muted">{s.city} · {s.availableConnectors} de {s.connectors.length} libres</span>
                </div>
                <div className="plates">
                  {s.connectors.map((c) => (
                    <button
                      key={c.connectorId}
                      className={`plate st-${c.status} ${selected === c.connectorId ? "picked" : ""}`}
                      onClick={() => setSelected(c.connectorId)}
                      aria-label={`${c.connectorId}, ${c.powerKw} kW, ${STATUS_TEXT[c.status]}`}
                    >
                      <span className="kw">{c.powerKw}<small>kW</small></span>
                      <span className="plate-id">{c.connectorId}</span>
                      <span className="plate-status">{STATUS_TEXT[c.status]}</span>
                    </button>
                  ))}
                </div>
              </div>
            )) ?? <p className="muted">Cargando estaciones…</p>}
          </section>

          <ReservePanel env={env} connector={selectedConnector} onDone={refresh} />
          <FlagsPanel env={env} data={data} onChange={refresh} />
          <TelemetryPanel env={env} connector={selectedConnector} onFinished={refresh} />
          <HistoryPanel env={env} sagas={data?.sagas ?? []} onRetry={refresh} />
        </main>
      )}
    </div>
  );
}

function EnvStatus({ data, error, env }: { data: Dashboard | null; error: string | null; env: Env }) {
  if (!data) return <div className="env-status muted">{error ? "Sin conexión" : `Conectando con ${env}…`}</div>;
  const services = Object.entries(data.environment.services);
  const down = services.filter(([, v]) => v !== "ok");
  return (
    <div className="env-status">
      <span>Versión {data.environment.version}</span>
      <span className={down.length ? "bad" : "good"}>
        {down.length ? `${down.length} servicio(s) caído(s)` : `${services.length} servicios en línea`}
      </span>
    </div>
  );
}

function ReservePanel({ env, connector, onDone }: { env: Env; connector: (Connector & { station: string }) | null; onDone: () => void }) {
  const [fleet, setFleet] = useState(FLEETS[0].id);
  const [kwh, setKwh] = useState(30);
  const [boost, setBoost] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [lastKey, setLastKey] = useState<string | null>(null);

  const start = async (repeatKey?: string) => {
    if (!connector) return;
    const key = repeatKey ?? crypto.randomUUID();
    setBusy(true);
    setResult(null);
    try {
      const r = await gql<{ reserveAndActivateCharge: { success: boolean; idempotent: boolean; message: string } }>(env, RESERVE_MUTATION, {
        input: { fleetId: fleet, connectorId: connector.connectorId, energyKwh: kwh, fastChargeBoost: boost, idempotencyKey: key },
      });
      const res = r.reserveAndActivateCharge;
      setLastKey(key);
      setResult({ ok: res.success, text: res.idempotent ? `${res.message} (respuesta repetida, sin cobro adicional)` : res.message });
      onDone();
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel reserve" aria-labelledby="reserve-title">
      <h2 id="reserve-title">Reservar y activar carga</h2>
      {connector ? (
        <p className="target">
          <strong>{connector.connectorId}</strong> en {connector.station}, {connector.powerKw} kW
        </p>
      ) : (
        <p className="muted">Elija una bahía disponible en el tablero.</p>
      )}
      <label>
        Flota
        <select value={fleet} onChange={(e) => setFleet(e.target.value)}>
          {FLEETS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </select>
      </label>
      <label>
        Energía a cargar: {kwh} kWh
        <input type="range" min={5} max={120} step={5} value={kwh} onChange={(e) => setKwh(Number(e.target.value))} />
      </label>
      <label className="check">
        <input type="checkbox" checked={boost} onChange={(e) => setBoost(e.target.checked)} />
        Prioridad de ultra-carga
      </label>
      <button className="primary" disabled={!connector || connector.status !== "AVAILABLE" || busy} onClick={() => start()}>
        {busy ? "Procesando…" : "Reservar y activar"}
      </button>
      {lastKey && (
        <button className="ghost" disabled={busy} onClick={() => start(lastKey)} title="Reenvía la misma mutación con el mismo idempotencyKey">
          Reenviar la misma solicitud
        </button>
      )}
      {result && <p className={`result ${result.ok ? "good" : "bad"}`} role="status">{result.text}</p>}
    </section>
  );
}

function FlagsPanel({ env, data, onChange }: { env: Env; data: Dashboard | null; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const toggle = async (name: string, enabled: boolean) => {
    try {
      setError(null);
      await gql(env, SET_FLAG_MUTATION, { name, enabled });
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section className="panel flags" aria-labelledby="flags-title">
      <h2 id="flags-title">Feature flags de {env}</h2>
      <p className="muted small">Se leen de <code>{data?.environment.tables["feature-flags"] ?? "…"}</code>. El cambio aplica en segundos, sin redesplegar.</p>
      <ul>
        {data?.featureFlags.map((f) => (
          <li key={f.name}>
            <label className="switch">
              <input type="checkbox" checked={f.enabled} onChange={(e) => toggle(f.name, e.target.checked)} />
              <span className="track" aria-hidden="true" />
              <span>
                {FLAG_TEXT[f.name] ?? f.name}
                <code className="flag-key">{f.name}</code>
                {f.updatedBy && <span className="muted small"> Cambiada por {f.updatedBy}</span>}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {error && <p className="result bad" role="alert">{error}</p>}
    </section>
  );
}

function TelemetryPanel({ env, connector, onFinished }: { env: Env; connector: Connector | null; onFinished: () => void }) {
  const [points, setPoints] = useState<Telemetry[]>([]);
  usePolling(() => {
    if (!connector) { setPoints([]); return; }
    gql<{ telemetry: Telemetry[] }>(env, TELEMETRY_QUERY, { id: connector.connectorId }).then((d) => setPoints(d.telemetry)).catch(() => setPoints([]));
  }, 3000, [env, connector?.connectorId, connector?.status]);

  const max = Math.max(1, ...points.map((p) => p.powerKw), connector?.powerKw ?? 1);
  const last = points[0];
  return (
    <section className="panel telemetry" aria-labelledby="tele-title">
      <h2 id="tele-title">Telemetría {connector ? connector.connectorId : ""}</h2>
      {!connector && <p className="muted">Elija una bahía para ver su consumo.</p>}
      {connector && !last && <p className="muted">Este conector aún no registra lecturas.</p>}
      {last && (
        <>
          <div className="readings">
            <div><span className="big">{last.powerKw.toFixed(1)}</span> kW</div>
            <div><span className="big">{last.energyDeliveredKwh.toFixed(2)}</span> kWh entregados</div>
            <div><span className="big">{last.voltageV.toFixed(0)}</span> V</div>
            <div><span className="big">{last.temperatureC.toFixed(0)}</span> °C</div>
          </div>
          <div className="spark" role="img" aria-label="Potencia de las últimas lecturas">
            {[...points].reverse().map((p) => <span key={p.recordedAt} style={{ height: `${(p.powerKw / max) * 100}%` }} title={`${p.powerKw} kW · ${time(p.recordedAt)}`} />)}
          </div>
        </>
      )}
      {connector?.status === "CHARGING" && (
        <button className="ghost" onClick={() => gql(env, FINISH_MUTATION, { id: connector.connectorId }).then(onFinished)}>Finalizar carga y liberar bahía</button>
      )}
      <WalletSummary env={env} />
    </section>
  );
}

function WalletSummary({ env }: { env: Env }) {
  const [wallets, setWallets] = useState<Wallet[]>([]);
  usePolling(() => {
    Promise.all(FLEETS.map((f) => gql<{ wallet: Wallet | null }>(env, WALLET_QUERY, { id: f.id }).then((d) => d.wallet).catch(() => null)))
      .then((ws) => setWallets(ws.filter(Boolean) as Wallet[]));
  }, 4000, [env]);
  return (
    <div className="wallets">
      <h3>Saldo de las flotas</h3>
      {wallets.map((w) => (
        <div key={w.fleetId} className="wallet-row"><span>{w.name}</span><strong>{cop.format(w.balance)}</strong></div>
      ))}
    </div>
  );
}

function HistoryPanel({ env, sagas, onRetry }: { env: Env; sagas: Saga[]; onRetry: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="panel history" aria-labelledby="history-title">
      <h2 id="history-title">Transacciones y compensaciones</h2>
      {sagas.length === 0 && <p className="muted">Todavía no hay transacciones en {env}. Reserve una bahía para iniciar la primera.</p>}
      <ol className="sagas">
        {sagas.map((s) => (
          <li key={s.sagaId} className={`saga s-${s.status}`}>
            <button className="saga-row" aria-expanded={open === s.sagaId} onClick={() => setOpen(open === s.sagaId ? null : s.sagaId)}>
              <span className="saga-state">{SAGA_TEXT[s.status] ?? s.status}</span>
              <span>{s.connectorId}</span>
              <span className="muted">{s.fleetId}</span>
              <span>{s.amount != null ? cop.format(s.amount) : "Sin cobro"}{s.surgeApplied ? " con tarifa pico" : ""}</span>
              <span className="muted">{time(s.createdAt)}</span>
              <code className="saga-id">{s.sagaId.slice(0, 8)}</code>
            </button>
            {open === s.sagaId && (
              <div className="saga-detail">
                {s.failureReason && <p><strong>Motivo:</strong> {s.failureReason}</p>}
                {s.compensations.length > 0 && (
                  <p><strong>Compensaciones:</strong> {s.compensations.map((c) => `${c.action === "REFUND" ? "Reembolso" : "Liberación"} (${c.result}${c.idempotent ? ", repetida" : ""})`).join(", luego ")}</p>
                )}
                <ol className="timeline">
                  {s.events.map((e, i) => (
                    <li key={i} className={`ev ev-${e.status}`}>
                      <span className="muted">{time(e.at)}</span>
                      <span>{e.step} / {e.action}</span>
                      <span className="ev-status">{e.status}</span>
                      {e.detail && <span className="muted">{e.detail}</span>}
                    </li>
                  ))}
                </ol>
                <p className="muted small">correlation_id <code>{s.sagaId}</code>, búsquelo en CloudWatch: <code>/aegisvolt/{env}/*</code></p>
                {s.status === "COMPENSATION_FAILED" && (
                  <button className="ghost" onClick={() => gql(env, RETRY_MUTATION, { id: s.sagaId }).then(onRetry)}>Reintentar compensación</button>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
