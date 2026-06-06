import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { API } from '../App';

const URGENCY_MAP = {
  critical: { label: 'CRITICAL', color: 'text-danger border-danger/30 bg-danger/5' },
  urgent:   { label: 'URGENT',   color: 'text-warn   border-warn/30   bg-warn/5'   },
  standard: { label: 'STANDARD', color: 'text-teal   border-teal/30   bg-teal/5'   },
};

function Row({ label, value, mono, highlight }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-border last:border-none">
      <span className="text-xs font-mono text-muted-fg uppercase tracking-widest">{label}</span>
      {highlight ? (
        <span className={`badge border ${highlight}`}>{value || '—'}</span>
      ) : (
        <span className={`text-sm ${mono ? 'font-mono text-blood' : 'text-foreground font-medium'}`}>
          {value || '—'}
        </span>
      )}
    </div>
  );
}

export default function DonorConfirm() {
  const [params]     = useSearchParams();
  const request_id   = params.get('request_id') || '';
  const donor_id     = params.get('donor_id')   || '';

  const [req,        setReq]        = useState(null);
  const [loadErr,    setLoadErr]    = useState(null);
  const [action,     setAction]     = useState(null);   // 'yes' | 'no'
  const [submitting, setSubmitting] = useState(false);
  const [visible,    setVisible]    = useState(false);

  useEffect(() => { setVisible(true); }, []);

  useEffect(() => {
    if (!request_id) return;
    fetch(`${API}/request/${request_id}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setReq)
      .catch(e => setLoadErr(e.message));
  }, [request_id]);

  const confirm = async (choice) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await fetch(
        `${API}/confirm?request_id=${encodeURIComponent(request_id)}&donor_id=${encodeURIComponent(donor_id)}&action=${choice}`
      );
      setAction(choice);
    } catch (e) {
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  };

  // ── Missing params ─────────────────────────────────────────────────────────
  if (!request_id || !donor_id) {
    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay">
        <div className="text-center space-y-3">
          <p className="font-display text-3xl text-foreground">Invalid link</p>
          <p className="text-xs font-mono text-muted-fg">This confirmation link is missing required parameters.</p>
        </div>
      </div>
    );
  }

  // ── Loading ────────────────────────────────────────────────────────────────
  if (!req && !loadErr) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <span className="text-xs font-mono text-muted-fg animate-pulse">Loading request...</span>
      </div>
    );
  }

  // ── Load error ─────────────────────────────────────────────────────────────
  if (loadErr) {
    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay">
        <div className="text-center space-y-3">
          <p className="font-display text-3xl text-foreground">Request not found</p>
          <p className="text-xs font-mono text-muted-fg">{loadErr}</p>
        </div>
      </div>
    );
  }

  const urgencyKey = (req.urgency || '').toLowerCase();
  const urgency    = URGENCY_MAP[urgencyKey] || URGENCY_MAP.standard;

  // ── Post-action: YES ───────────────────────────────────────────────────────
  if (action === 'yes') {
    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
        <div className="fixed inset-0 grid-lines pointer-events-none opacity-20" />
        <div
          className={`relative z-10 max-w-sm w-full border border-safe/30 bg-card rounded p-8 glow-safe transition-all duration-700 ${
            visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'
          }`}
        >
          <div className="mb-6">
            <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-4">
              <span className="w-6 h-px bg-safe/40" />
              Confirmed
            </span>
            <h1 className="font-display text-4xl text-foreground leading-tight">
              You're saving<br />a life.
            </h1>
          </div>

          <p className="text-sm text-muted-fg leading-relaxed mb-6">
            Thank you. Please make your way to{' '}
            <strong className="text-foreground">{req.hospital_name}</strong>{' '}
            as soon as possible. The hospital team has been notified.
          </p>

          <div className="border border-border rounded divide-y divide-border">
            <Row label="Blood Group" value={req.blood_group} />
            <Row label="Hospital"    value={req.hospital_name} />
            <Row label="Urgency"     value={urgency.label} highlight={urgency.color} />
            <Row label="Request ID"  value={request_id}    mono />
          </div>

          <p className="text-[10px] font-mono text-muted-fg mt-4">
            Show this screen or your request ID at the hospital reception.
          </p>
        </div>
      </div>
    );
  }

  // ── Post-action: NO ────────────────────────────────────────────────────────
  if (action === 'no') {
    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
        <div
          className={`relative z-10 max-w-sm w-full text-center transition-all duration-700 ${
            visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'
          }`}
        >
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-6">
            <span className="w-6 h-px bg-foreground/20" />
            Response recorded
          </span>
          <h1 className="font-display text-4xl text-foreground leading-tight mb-4">
            Thank you for<br />letting us know.
          </h1>
          <p className="text-sm text-muted-fg leading-relaxed">
            We'll contact another eligible donor immediately.
            If your situation changes, please reach out to the hospital directly.
          </p>
        </div>
      </div>
    );
  }

  // ── Default: confirmation prompt ───────────────────────────────────────────
  return (
    <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
      <div className="fixed inset-0 grid-lines pointer-events-none opacity-20" />

      <div
        className={`relative z-10 max-w-sm w-full transition-all duration-700 ${
          visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'
        }`}
      >
        {/* Header */}
        <div className="mb-8">
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-5">
            <span className="w-1.5 h-1.5 rounded-full bg-blood pulse-blood" />
            Blood donation request
          </span>
          <h1 className="font-display text-5xl text-foreground leading-tight tracking-tight">
            A patient<br />needs you.
          </h1>
        </div>

        {/* Urgency banner — critical only */}
        {urgencyKey === 'critical' && (
          <div className="border border-danger/30 bg-danger/5 px-4 py-3 rounded mb-5 flex items-center gap-2 glow-blood">
            <span className="w-1.5 h-1.5 rounded-full bg-danger pulse-blood" />
            <span className="text-danger text-xs font-mono uppercase tracking-widest">
              Critical — every minute matters
            </span>
          </div>
        )}

        {/* Request details */}
        <div className="border border-border rounded divide-y divide-border mb-6 bg-card">
          <Row label="Blood Group" value={req.blood_group} />
          <Row label="Hospital"    value={req.hospital_name} />
          <Row label="Urgency"     value={urgency.label} highlight={urgency.color} />
          <Row label="Request ID"  value={request_id}    mono />
        </div>

        {/* Action buttons */}
        <div className="grid grid-cols-2 gap-3 mb-4">
          <button
            onClick={() => confirm('yes')}
            disabled={submitting}
            className="border border-safe/40 bg-safe/5 text-safe py-4 rounded font-medium text-sm hover:bg-safe/10 active:scale-95 transition-all disabled:opacity-40 flex flex-col items-center gap-1.5"
          >
            <span className="text-xl">✓</span>
            <span>Yes, I can donate</span>
          </button>
          <button
            onClick={() => confirm('no')}
            disabled={submitting}
            className="border border-border bg-card text-muted-fg py-4 rounded font-medium text-sm hover:border-danger/40 hover:text-danger active:scale-95 transition-all disabled:opacity-40 flex flex-col items-center gap-1.5"
          >
            <span className="text-xl">✕</span>
            <span>I'm unavailable</span>
          </button>
        </div>

        <p className="text-center text-[10px] font-mono text-muted-fg">
          Your response is sent immediately to the BloodLink coordinator.
        </p>
      </div>
    </div>
  );
}
