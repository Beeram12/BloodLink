import React, { useEffect, useState, useCallback } from 'react';
import { API, useAuth } from '../App';

// ── Helpers ───────────────────────────────────────────────────────────────────

function elapsed(iso) {
  if (!iso) return '—';
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function daysColor(days) {
  if (days === null || days === undefined) return 'text-muted-fg';
  if (days <= 3)  return 'text-danger font-bold';
  if (days <= 7)  return 'text-orange-500';
  if (days <= 14) return 'text-warn';
  return 'text-safe';
}

// ── Badges ────────────────────────────────────────────────────────────────────

function HealthBadge({ status }) {
  const map = {
    GREEN:  { color: 'text-safe   border-safe/30   bg-safe/5',   dot: 'bg-safe' },
    YELLOW: { color: 'text-warn   border-warn/30   bg-warn/5',   dot: 'bg-warn' },
    RED:    { color: 'text-danger border-danger/30 bg-danger/5', dot: 'bg-danger animate-pulse' },
    BLUE:   { color: 'text-teal   border-teal/30   bg-teal/5',   dot: 'bg-teal' },
  };
  const c = map[status] || { color: 'text-muted-fg border-border bg-muted', dot: 'bg-muted-fg' };
  return (
    <span className={`badge border ${c.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {status}
    </span>
  );
}

function UrgencyBadge({ urgency }) {
  const map = {
    CRITICAL:  'bg-danger/10 text-danger border-danger/30',
    URGENT:    'bg-orange-500/10 text-orange-400 border-orange-500/30',
    SOON:      'bg-warn/10 text-warn border-warn/30',
    SCHEDULED: 'bg-safe/10 text-safe border-safe/30',
  };
  return (
    <span className={`badge border text-[10px] ${map[urgency] || 'bg-muted text-muted-fg border-border'}`}>
      {urgency}
    </span>
  );
}

// ── Donor row ─────────────────────────────────────────────────────────────────

function DonorRow({ donor, showDistance, onContact }) {
  const { user_id, blood_group, active, calls_to_donations_ratio,
          donations_till_date, location_name, gap_days, distance_km,
          classification, next_eligible_date } = donor;

  const borderColor = { GREEN: 'border-l-safe', YELLOW: 'border-l-warn', RED: 'border-l-danger', BLUE: 'border-l-teal' }[classification] || 'border-l-border';

  return (
    <div className={`flex items-center justify-between px-4 py-3 border-b border-border/50 border-l-2 ${borderColor} hover:bg-muted/20 transition-colors`}>
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <HealthBadge status={classification} />
        <span className="font-mono text-xs text-foreground truncate max-w-[130px]" title={user_id}>
          {(user_id || '').slice(0, 16)}…
        </span>
        <span className="font-mono text-xs border border-border px-1.5 py-0.5 text-foreground hidden sm:inline">{blood_group}</span>
        <span className={`text-xs font-mono hidden md:inline ${active ? 'text-safe' : 'text-danger'}`}>{active ? 'Active' : 'Inactive'}</span>
        <span className="text-xs text-muted-fg hidden lg:inline truncate max-w-[100px]">{location_name || 'Hyderabad Area'}</span>
      </div>
      <div className="flex items-center gap-3 text-xs font-mono text-muted-fg shrink-0 ml-3">
        {next_eligible_date && <span className="hidden xl:inline">elig: {next_eligible_date}</span>}
        {gap_days !== null && gap_days !== undefined && (
          <span className={gap_days >= 0 ? 'text-safe' : 'text-danger'}>gap {gap_days >= 0 ? '+' : ''}{gap_days}d</span>
        )}
        {showDistance && distance_km != null && <span>{distance_km}km</span>}
        {calls_to_donations_ratio && <span className="hidden sm:inline">r:{parseFloat(calls_to_donations_ratio).toFixed(1)}</span>}
        {donations_till_date && <span className="hidden sm:inline">{donations_till_date}★</span>}
        {onContact && (
          <button
            onClick={() => onContact(donor)}
            className="text-[10px] font-mono bg-muted border border-border px-3 py-1.5 rounded hover:border-teal/60 hover:text-teal transition-colors ml-1"
          >
            Contact
          </button>
        )}
      </div>
    </div>
  );
}

// ── Volunteer Search ──────────────────────────────────────────────────────────

function VolunteerSearch() {
  const [query,     setQuery]     = useState('');
  const [loading,   setLoading]   = useState(false);
  const [result,    setResult]    = useState(null);
  const [error,     setError]     = useState('');
  const [contacted, setContacted] = useState('');

  const runSearch = async (uid) => {
    const q = (uid || query).trim();
    if (!q) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const r = await fetch(`${API}/patient/search?user_id=${encodeURIComponent(q)}`);
      const d = await r.json();
      if (!r.ok) setError(d.error || 'User not found');
      else { setQuery(q); setResult(d); }
    } catch {
      setError('Network error — please try again');
    } finally {
      setLoading(false);
    }
  };

  const handleContact = async (donor) => {
    if (!result) return;
    const { patient } = result;
    try {
      const r = await fetch(`${API}/request`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          patient_user_id: patient.user_id,
          blood_group:     patient.blood_group,
          hospital_name:   patient.location_name || 'Hyderabad Area',
          urgency:         (patient.urgency || 'urgent').toLowerCase(),
          donor_id:        donor.donor_id || donor.user_id,
        }),
      });
      const d = await r.json();
      setContacted(`Request ${d.request_id} created`);
      setTimeout(() => setContacted(''), 6000);
    } catch {
      setContacted('Failed to create request');
    }
  };

  const alertStyle = {
    green:  'bg-safe/10   border-safe/30   text-safe',
    yellow: 'bg-warn/10   border-warn/30   text-warn',
    orange: 'bg-orange-500/10 border-orange-500/30 text-orange-400',
    blue:   'bg-teal/10   border-teal/30   text-teal',
  };

  return (
    <div className="space-y-5">
      {/* Search bar */}
      <form onSubmit={e => { e.preventDefault(); runSearch(); }} className="flex gap-3">
        <input
          type="text"
          value={query}
          onChange={e => { setQuery(e.target.value); setResult(null); setError(''); }}
          placeholder="Enter patient user_id to look up donors…"
          className="flex-1 bg-muted border border-border text-foreground font-mono text-sm px-4 py-3 rounded focus:outline-none focus:border-teal/60 placeholder:text-muted-fg"
        />
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="text-sm font-mono border border-teal/50 text-teal px-8 py-3 rounded hover:bg-teal/10 transition-colors disabled:opacity-40"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

      {error && (
        <div className="border border-danger/30 bg-danger/5 text-danger text-xs font-mono px-4 py-3 rounded">
          {error}
        </div>
      )}
      {contacted && (
        <div className="border border-safe/30 bg-safe/5 text-safe text-xs font-mono px-4 py-3 rounded">
          {contacted}
        </div>
      )}

      {/* Results */}
      {result && (() => {
        const { patient, alert, green_donors = [], yellow_donors = [], red_donors = [], blue_donors = [] } = result;
        const groups = [
          { key: 'green',  donors: green_donors,  label: 'Bridge Donors — Ready',     border: 'border-l-safe',   badge: 'GREEN' },
          { key: 'yellow', donors: yellow_donors, label: 'Bridge Donors — Unreliable', border: 'border-l-warn',   badge: 'YELLOW' },
          { key: 'red',    donors: red_donors,    label: 'Bridge Donors — Inactive',   border: 'border-l-danger', badge: 'RED' },
          { key: 'blue',   donors: blue_donors,   label: 'Emergency Donor Pool',       border: 'border-l-teal',   badge: 'BLUE' },
        ].filter(g => g.donors.length > 0);

        return (
          <>
            {/* Patient banner */}
            <div className="border border-border bg-card px-6 py-5">
              <div className="flex items-start justify-between flex-wrap gap-4">
                <div className="space-y-1.5">
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="font-mono text-xs border border-border px-2 py-0.5 text-foreground">{patient.blood_group}</span>
                    {patient.gender && <span className="text-xs text-muted-fg">{patient.gender}</span>}
                    <UrgencyBadge urgency={patient.urgency} />
                  </div>
                  <div className="text-sm text-foreground">{patient.location_name || 'Hyderabad Area'}</div>
                  {patient.frequency_in_days && (
                    <div className="text-xs font-mono text-muted-fg">Transfusion every {patient.frequency_in_days} days</div>
                  )}
                </div>
                <div className="text-right space-y-1">
                  <div className="text-xs font-mono text-muted-fg">Next: {patient.expected_next_transfusion_date || '—'}</div>
                  {patient.days_until_transfusion != null && (
                    <div className={`text-sm font-mono ${daysColor(patient.days_until_transfusion)}`}>
                      {patient.days_until_transfusion} days away
                    </div>
                  )}
                </div>
              </div>
              {alert && (
                <div className={`border rounded px-4 py-2.5 text-xs font-mono mt-4 ${alertStyle[alert.level] || ''}`}>
                  {alert.message}
                </div>
              )}
            </div>

            {/* Donor sections */}
            {groups.map(({ key, donors, label, border, badge }) => (
              <div key={key} className={`border border-border rounded overflow-hidden border-l-4 ${border}`}>
                <div className="px-5 py-3 bg-muted/30 border-b border-border flex items-center gap-3">
                  <HealthBadge status={badge} />
                  <span className="text-xs font-mono text-muted-fg">{label} — {donors.length}</span>
                </div>
                {donors.map((d, i) => (
                  <DonorRow
                    key={d.user_id || d.donor_id || i}
                    donor={d}
                    showDistance={key === 'blue'}
                    onContact={handleContact}
                  />
                ))}
              </div>
            ))}
          </>
        );
      })()}
    </div>
  );
}

// ── Status progress bar ───────────────────────────────────────────────────────

const STEPS = ['SEARCHING', 'DONOR_FOUND', 'ARRIVING', 'TRANSFUSING', 'COMPLETED'];

const STEP_CONFIG = {
  SEARCHING:   { label: 'Searching',   dot: 'bg-danger',     ring: 'ring-danger/40',   animate: 'animate-pulse' },
  DONOR_FOUND: { label: 'Donor Found', dot: 'bg-safe',       ring: 'ring-safe/40',     animate: '' },
  ARRIVING:    { label: 'Arriving',    dot: 'bg-teal',       ring: 'ring-teal/40',     animate: '' },
  TRANSFUSING: { label: 'Transfusing', dot: 'bg-orange-500', ring: 'ring-orange-400/40', animate: '' },
  COMPLETED:   { label: 'Completed',   dot: 'bg-foreground', ring: 'ring-foreground/20', animate: '' },
};

// Volunteer can only mark DONOR_FOUND — remaining steps are donor-controlled
const NEXT_LABEL = {
  DONOR_FOUND: 'Confirm Donor Found',
};

function StatusProgress({ status, requestId, onUpdated }) {
  const currentIdx = STEPS.indexOf(status);
  const [busy, setBusy] = useState(false);
  const nextStatus = currentIdx >= 0 && currentIdx < STEPS.length - 1 ? STEPS[currentIdx + 1] : null;
  const nextLabel  = NEXT_LABEL[nextStatus];

  const advance = async () => {
    if (!nextStatus) return;
    setBusy(true);
    try {
      await fetch(`${API}/request/${requestId}/status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_status: nextStatus }),
      });
      onUpdated();
    } catch (e) { console.error(e); }
    finally { setBusy(false); }
  };

  return (
    <div className="mt-5">
      {/* Progress track */}
      <div className="flex items-center">
        {STEPS.map((step, i) => {
          const cfg      = STEP_CONFIG[step];
          const done     = i < currentIdx;
          const active   = i === currentIdx;
          const upcoming = i > currentIdx;
          return (
            <React.Fragment key={step}>
              {/* Step dot + label */}
              <div className="flex flex-col items-center gap-1.5 shrink-0">
                <div className={`
                  w-4 h-4 rounded-full flex items-center justify-center
                  ring-2 transition-all duration-300
                  ${done    ? `${cfg.dot} ring-transparent` : ''}
                  ${active  ? `${cfg.dot} ${cfg.ring} ${cfg.animate}` : ''}
                  ${upcoming ? 'bg-muted border-2 border-border ring-transparent' : ''}
                `}>
                  {done && (
                    <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </div>
                <span className={`text-[9px] font-mono tracking-wide whitespace-nowrap ${
                  done    ? 'text-foreground/60' :
                  active  ? 'text-foreground font-semibold' :
                            'text-muted-fg/40'
                }`}>
                  {cfg.label}
                </span>
              </div>
              {/* Connector line */}
              {i < STEPS.length - 1 && (
                <div className={`flex-1 h-0.5 mx-1 mb-4 rounded transition-all duration-500 ${
                  done ? 'bg-foreground/40' : 'bg-border'
                }`} />
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Advance button */}
      {nextLabel && (
        <button onClick={advance} disabled={busy}
          className="mt-3 text-[11px] font-mono border border-teal/40 text-teal px-4 py-2 rounded hover:bg-teal/10 active:scale-95 transition-all disabled:opacity-40">
          {busy ? 'Updating…' : `→ ${nextLabel}`}
        </button>
      )}
      {status === 'COMPLETED' && (
        <span className="mt-3 inline-block text-[11px] font-mono text-safe">✓ Transfusion completed</span>
      )}
    </div>
  );
}

// ── Pipeline page ─────────────────────────────────────────────────────────────

export default function Pipeline() {
  const { logout }    = useAuth();
  const [requests, setRequests] = useState([]);
  const [loadingReq, setLoadingReq] = useState(true);
  const [nightly,  setNightly]  = useState(null);
  const [visible,  setVisible]  = useState(false);

  useEffect(() => { setVisible(true); }, []);

  const fetchRequests = useCallback(async () => {
    try {
      const r = await fetch(`${API}/requests/active`);
      const d = await r.json();
      setRequests(Array.isArray(d) ? d : []);
    } catch (e) {
      console.error('Requests fetch error:', e);
    } finally {
      setLoadingReq(false);
    }
  }, []);

  useEffect(() => {
    fetchRequests();
    const id = setInterval(fetchRequests, 10000);
    return () => clearInterval(id);
  }, [fetchRequests]);

  const handleNightly = async () => {
    setNightly('running');
    try {
      const r = await fetch(`${API}/nightly/run`, { method: 'POST' });
      const d = await r.json();
      setNightly(`Done — ${d.processed} processed, ${d.failed} failed`);
      fetchRequests();
    } catch { setNightly('Error'); }
    setTimeout(() => setNightly(null), 5000);
  };

  return (
    <div className="noise-overlay pt-20">
      <div className="fixed inset-0 grid-lines pointer-events-none opacity-30 z-0" />

      <div className="relative z-10 max-w-5xl mx-auto px-6 lg:px-12 py-12 space-y-14">

        {/* Header */}
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
              <button onClick={handleNightly} disabled={nightly === 'running'}
                className="text-xs font-mono border border-border text-muted-fg px-4 py-2 rounded hover:border-teal/50 hover:text-teal transition-colors disabled:opacity-40">
                {nightly === 'running' ? '⟳ Running...' : 'Run nightly check'}
              </button>
              <button onClick={logout}
                className="text-xs font-mono text-muted-fg hover:text-danger transition-colors">
                Sign out
              </button>
            </div>
          </div>
          {nightly && nightly !== 'running' && (
            <p className="text-xs font-mono text-safe mt-3">{nightly}</p>
          )}
        </div>

        {/* ── Volunteer patient lookup ──────────────────────────────── */}
        <section>
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-6">
            <span className="w-6 h-px bg-foreground/20" />
            Volunteer patient lookup
          </span>
          <VolunteerSearch />
        </section>

        {/* ── Live requests ─────────────────────────────────────────── */}
        <section>
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-6">
            <span className="w-6 h-px bg-foreground/20" />
            Live requests — {requests.length} active
          </span>

          {loadingReq ? (
            <div className="text-xs font-mono text-muted-fg animate-pulse">Loading requests…</div>
          ) : requests.length === 0 ? (
            <div className="border border-border p-8 text-center text-muted-fg text-xs font-mono rounded">
              No active requests.
            </div>
          ) : (
            <div className="border border-border rounded overflow-hidden divide-y divide-border">
              {requests.map((req) => (
                <div key={req.request_id}
                  className={`px-6 py-5 hover:bg-muted/10 transition-colors ${req.status === 'NEEDS_HUMAN' ? 'glow-blood' : ''}`}>
                  <div className="flex items-start justify-between flex-wrap gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-3 flex-wrap">
                        <span className="font-mono text-blood text-xs">{req.request_id}</span>
                        <span className="font-mono text-xs border border-border px-2 py-0.5 text-foreground">{req.blood_group}</span>
                        <UrgencyBadge urgency={(req.urgency || '').toUpperCase()} />
                      </div>
                      <div className="text-xs text-muted-fg">
                        {req.hospital_name} · L{req.escalation_level} · {elapsed(req.created_at)} ago
                      </div>
                    </div>
                    <span className="text-[10px] font-mono text-muted-fg">
                      {STEPS.includes(req.status) ? req.status.replace('_', ' ') : 'SEARCHING'}
                    </span>
                  </div>
                  <StatusProgress status={STEPS.includes(req.status) ? req.status : 'SEARCHING'} requestId={req.request_id} onUpdated={fetchRequests} />
                  {req.donor_response && (
                    <div className="mt-4 border border-safe/20 bg-safe/5 rounded px-4 py-3 text-xs font-mono space-y-1">
                      <div className="text-safe text-[10px] uppercase tracking-widest mb-2">Donor Response</div>
                      {req.donor_response.location     && <div><span className="text-muted-fg">Location: </span><span className="text-foreground">{req.donor_response.location}</span></div>}
                      {req.donor_response.arrival_time && <div><span className="text-muted-fg">ETA: </span><span className="text-foreground">{req.donor_response.arrival_time}</span></div>}
                      {req.donor_response.transport    && <div><span className="text-muted-fg">Transport: </span><span className="text-foreground">{req.donor_response.needs_pickup ? '🚗 Pickup needed' : 'Self transport'}</span></div>}
                      <div className="text-muted-fg/60 text-[9px] mt-1">{req.donor_response.submitted_at ? new Date(req.donor_response.submitted_at).toLocaleTimeString() : ''}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

      </div>
    </div>
  );
}
