import React, { useEffect, useState, useCallback } from 'react';
import { API, useAuth } from '../App';

// ── Helpers ──────────────────────────────────────────────────────────────────

function elapsed(iso) {
  if (!iso) return '—';
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60)  return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

function StatusBadge({ status }) {
  const map = {
    CONFIRMED:   { dot: 'bg-safe',   label: 'CONFIRMED' },
    PENDING:     { dot: 'bg-warn',   label: 'PENDING' },
    NEEDS_HUMAN: { dot: 'bg-danger', label: 'NEEDS HUMAN' },
  };
  const c = map[status] || { dot: 'bg-muted-fg', label: status };
  return (
    <span className="badge border border-border text-muted-fg bg-muted flex items-center gap-1.5">
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {c.label}
    </span>
  );
}

function HealthBadge({ status }) {
  const map = {
    GREEN:  { color: 'text-safe   border-safe/30   bg-safe/5',   dot: 'bg-safe   pulse-blood' },
    YELLOW: { color: 'text-warn   border-warn/30   bg-warn/5',   dot: 'bg-warn' },
    RED:    { color: 'text-danger border-danger/30 bg-danger/5', dot: 'bg-danger pulse-blood' },
  };
  const c = map[status] || { color: 'text-muted-fg border-border bg-muted', dot: 'bg-muted-fg' };
  return (
    <span className={`badge border ${c.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {status}
    </span>
  );
}

// ── Animated counter (mirrors Optimus MetricsSection) ────────────────────────
function Counter({ end }) {
  const [count, setCount] = useState(0);
  const [ran,   setRan]   = useState(false);
  const ref = React.useRef(null);

  useEffect(() => {
    const observer = new IntersectionObserver(([e]) => {
      if (e.isIntersecting && !ran) {
        setRan(true);
        const start = performance.now();
        const dur   = 1200;
        const step  = (now) => {
          const p = Math.min((now - start) / dur, 1);
          const e = 1 - Math.pow(1 - p, 3);
          setCount(Math.floor(e * end));
          if (p < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      }
    }, { threshold: 0.5 });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, [end, ran]);

  return <span ref={ref}>{count}</span>;
}

// ── Stat tile ─────────────────────────────────────────────────────────────────
function StatTile({ label, value, accent }) {
  return (
    <div className="border border-border bg-card p-6 lg:p-8 hover-lift group">
      <div className={`font-display text-5xl lg:text-6xl tracking-tight mb-3 ${accent || 'text-foreground'}`}>
        <Counter end={typeof value === 'number' ? value : 0} />
      </div>
      <div className="text-xs font-mono text-muted-fg uppercase tracking-widest">{label}</div>
    </div>
  );
}

// ── Bridge row ────────────────────────────────────────────────────────────────
function BridgeRow({ bridge, onApprove }) {
  const [open, setOpen] = useState(bridge.health_status === 'RED');
  const { bridge_id, hospital_name, blood_group, health_status,
          next_transfusion_date, calls_to_donations_ratio,
          replacement_candidates = [] } = bridge;

  return (
    <div className={`border-b border-border ${health_status === 'RED' ? 'glow-blood' : ''}`}>
      {/* Summary row */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-6 py-4 hover:bg-muted/30 transition-colors text-left"
      >
        <div className="flex items-center gap-4 min-w-0">
          <HealthBadge status={health_status} />
          <span className="font-mono text-xs text-blood">{bridge_id}</span>
          <span className="text-sm text-foreground truncate">{hospital_name}</span>
          <span className="font-mono text-xs text-muted-fg border border-border px-2 py-0.5 rounded hidden sm:inline">
            {blood_group}
          </span>
        </div>
        <div className="flex items-center gap-6 text-xs font-mono text-muted-fg shrink-0 ml-4">
          {next_transfusion_date && <span>next: {next_transfusion_date}</span>}
          {calls_to_donations_ratio && (
            <span>ratio: {parseFloat(calls_to_donations_ratio || 0).toFixed(1)}</span>
          )}
          <span className="text-border">{open ? '↑' : '↓'}</span>
        </div>
      </button>

      {/* Replacement candidates (RED bridges) */}
      {open && health_status === 'RED' && (
        <div className="px-6 pb-5 space-y-2 border-t border-border/50 pt-4">
          {replacement_candidates.length === 0 ? (
            <p className="text-xs font-mono text-muted-fg">No replacement candidates found.</p>
          ) : (
            replacement_candidates.map((c, i) => (
              <div key={c.donor_id || i}
                className="flex items-center justify-between border border-border bg-card px-4 py-3 rounded">
                <div className="text-sm">
                  <span className="text-foreground font-medium">{c.name || c.donor_id}</span>
                  <span className="ml-3 font-mono text-xs text-muted-fg">{c.blood_group}</span>
                  {c.city && <span className="ml-2 text-xs text-teal">{c.city}</span>}
                </div>
                <button
                  onClick={() => onApprove(bridge_id, c.donor_id)}
                  className="text-xs font-mono bg-muted border border-border text-foreground px-3 py-1.5 rounded hover:border-teal/60 hover:text-teal transition-colors"
                >
                  Approve replacement
                </button>
              </div>
            ))
          )}
        </div>
      )}

      {/* Yellow/Green detail */}
      {open && health_status !== 'RED' && (
        <div className="px-6 pb-4 pt-3 border-t border-border/50">
          <p className="text-xs font-mono text-muted-fg">
            Bridge is {health_status === 'GREEN' ? 'healthy' : 'approaching threshold'}.
            {next_transfusion_date && ` Next transfusion: ${next_transfusion_date}.`}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Pipeline page ─────────────────────────────────────────────────────────────
export default function Pipeline() {
  const { logout } = useAuth();
  const [bridges,  setBridges]  = useState([]);
  const [requests, setRequests] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [nightly,  setNightly]  = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [visible,  setVisible]  = useState(false);

  useEffect(() => { setVisible(true); }, []);

  const fetchData = useCallback(async () => {
    try {
      const [bR, rR] = await Promise.all([
        fetch(`${API}/bridges/health`),
        fetch(`${API}/requests/active`),
      ]);
      const [b, r] = await Promise.all([bR.json(), rR.json()]);
      setBridges(Array.isArray(b) ? b : []);
      setRequests(Array.isArray(r) ? r : []);
      setLastRefresh(new Date());
    } catch (err) {
      console.error('Pipeline fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 10000);
    return () => clearInterval(id);
  }, [fetchData]);

  const handleApprove = async (bridge_id, replacement_donor_id) => {
    await fetch(`${API}/bridges/approve`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ bridge_id, replacement_donor_id }),
    }).catch(console.error);
    await fetchData();
  };

  const handleNightly = async () => {
    setNightly('running');
    try {
      const r = await fetch(`${API}/nightly/run`, { method: 'POST' });
      const d = await r.json();
      setNightly(`Done — ${d.processed} processed, ${d.failed} failed`);
      await fetchData();
    } catch {
      setNightly('Error running check');
    }
    setTimeout(() => setNightly(null), 6000);
  };

  const counts = {
    GREEN:  bridges.filter(b => b.health_status === 'GREEN').length,
    YELLOW: bridges.filter(b => b.health_status === 'YELLOW').length,
    RED:    bridges.filter(b => b.health_status === 'RED').length,
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen text-muted-fg font-mono text-xs">
        <span className="animate-pulse">Loading pipeline...</span>
      </div>
    );
  }

  return (
    <div className="noise-overlay pt-20">
      {/* Grid */}
      <div className="fixed inset-0 grid-lines pointer-events-none opacity-30 z-0" />

      <div className="relative z-10 max-w-6xl mx-auto px-6 lg:px-12 py-12 space-y-16">

        {/* ── Header ───────────────────────────────────────────────── */}
        <div className={`transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-5">
            <span className="w-6 h-px bg-foreground/20" />
            Operations pipeline
          </span>
          <div className="flex items-end justify-between flex-wrap gap-4">
            <h1 className="font-display text-5xl lg:text-7xl text-foreground leading-tight tracking-tight">
              Warrior<br />Dashboard
            </h1>
            <div className="flex items-center gap-4 pb-2">
              {lastRefresh && (
                <span className="text-[10px] font-mono text-muted-fg hidden sm:block">
                  ↻ {lastRefresh.toLocaleTimeString()}
                </span>
              )}
              <button
                onClick={handleNightly}
                disabled={nightly === 'running'}
                className="text-xs font-mono border border-border text-muted-fg px-4 py-2 rounded hover:border-teal/50 hover:text-teal transition-colors disabled:opacity-40"
              >
                {nightly === 'running' ? '⟳ Running...' : 'Run nightly check'}
              </button>
            </div>
          </div>
          {nightly && nightly !== 'running' && (
            <p className="text-xs font-mono text-safe mt-3">{nightly}</p>
          )}
        </div>

        {/* ── Metrics grid (Optimus-style gap-px grid) ─────────────── */}
        <section>
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-6">
            <span className="w-6 h-px bg-foreground/20" />
            Bridge health
          </span>
          <div className="grid grid-cols-3 gap-px bg-border">
            <StatTile label="Green bridges"  value={counts.GREEN}  accent="text-safe" />
            <StatTile label="Yellow bridges" value={counts.YELLOW} accent="text-warn" />
            <StatTile label="Red bridges"    value={counts.RED}    accent="text-danger" />
          </div>
        </section>

        {/* ── Bridge list ───────────────────────────────────────────── */}
        <section>
          <div className="flex items-center justify-between mb-6">
            <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg">
              <span className="w-6 h-px bg-foreground/20" />
              Bridge records — {bridges.length} total
            </span>
          </div>
          <div className="border border-border rounded overflow-hidden">
            {bridges.length === 0 ? (
              <div className="p-8 text-center text-muted-fg text-xs font-mono">
                No bridge records found.
              </div>
            ) : (
              bridges.map(b => (
                <BridgeRow key={b.bridge_id} bridge={b} onApprove={handleApprove} />
              ))
            )}
          </div>
        </section>

        {/* ── Active requests (Optimus metrics grid style) ──────────── */}
        <section>
          <div className="flex items-center justify-between mb-6">
            <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg">
              <span className="w-6 h-px bg-foreground/20" />
              Live requests — {requests.length} active
            </span>
          </div>

          {requests.length === 0 ? (
            <div className="border border-border p-8 text-center text-muted-fg text-xs font-mono rounded">
              No active requests.
            </div>
          ) : (
            <div className="border border-border rounded overflow-hidden">
              {/* Header */}
              <div className="grid grid-cols-6 border-b border-border px-6 py-3 text-[10px] font-mono text-muted-fg uppercase tracking-widest bg-muted/30">
                <span>Request ID</span>
                <span>Blood Group</span>
                <span>Hospital</span>
                <span>Status</span>
                <span>Level</span>
                <span>Elapsed</span>
              </div>
              {/* Rows */}
              {requests.map((req, i) => (
                <div
                  key={req.request_id}
                  className={`grid grid-cols-6 items-center px-6 py-4 border-b border-border/50 hover:bg-muted/20 transition-colors text-sm ${
                    req.status === 'NEEDS_HUMAN' ? 'glow-blood' : ''
                  }`}
                >
                  <span className="font-mono text-blood text-xs truncate">{req.request_id}</span>
                  <span>
                    <span className="font-mono text-xs border border-border px-2 py-0.5 rounded text-foreground">
                      {req.blood_group}
                    </span>
                  </span>
                  <span className="text-foreground text-xs truncate">{req.hospital_name}</span>
                  <span><StatusBadge status={req.status} /></span>
                  <span className="font-mono text-xs text-muted-fg">L{req.escalation_level}</span>
                  <span className="font-mono text-xs text-muted-fg">{elapsed(req.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── Footer spacer ─────────────────────────────────────────── */}
        <div className="pb-8 border-t border-border pt-6 flex items-center justify-between">
          <span className="text-[10px] font-mono text-muted-fg">
            Auto-refresh every 10s · BloodLink Pipeline
          </span>
          <button
            onClick={logout}
            className="text-[10px] font-mono text-muted-fg hover:text-danger transition-colors"
          >
            Sign out
          </button>
        </div>

      </div>
    </div>
  );
}
