import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { API } from '../App';

// ── Follow-up questions after YES ────────────────────────────────────────────

const QUESTIONS = [
  {
    id:      'can_donate',
    text:    'Can you donate blood right now?',
    type:    'choice',
    options: ['Yes, I am available', 'No, I am unavailable'],
  },
  {
    id:      'location',
    text:    'What is your current location / area?',
    type:    'text',
    placeholder: 'e.g. Banjara Hills, Hyderabad',
  },
  {
    id:      'transport',
    text:    'How will you reach the hospital?',
    type:    'choice',
    options: ['I will come on my own', 'I need a pickup arranged'],
  },
  {
    id:      'arrival_time',
    text:    'When can you arrive at the hospital?',
    type:    'choice',
    options: ['Within 30 minutes', 'Within 1 hour', 'Within 2 hours', 'More than 2 hours'],
  },
];

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

// ── Question step component ───────────────────────────────────────────────────

function QuestionStep({ question, onAnswer, stepNum, totalSteps }) {
  const [text, setText] = useState('');

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-[10px] font-mono text-muted-fg">
        <span>Step {stepNum} of {totalSteps}</span>
        <div className="flex gap-1">
          {Array.from({ length: totalSteps }).map((_, i) => (
            <div key={i} className={`h-0.5 w-6 rounded ${i < stepNum ? 'bg-safe' : 'bg-border'}`} />
          ))}
        </div>
      </div>

      <p className="text-lg text-foreground font-medium leading-snug">{question.text}</p>

      {question.type === 'choice' ? (
        <div className="space-y-2">
          {question.options.map((opt) => (
            <button
              key={opt}
              onClick={() => onAnswer(opt)}
              className="w-full text-left border border-border bg-card px-4 py-3.5 rounded text-sm text-foreground hover:border-teal/50 hover:bg-teal/5 active:scale-[0.99] transition-all"
            >
              {opt}
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          <input
            type="text"
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={question.placeholder}
            className="w-full bg-muted border border-border text-foreground font-mono text-sm px-4 py-3 rounded focus:outline-none focus:border-teal/60 placeholder:text-muted-fg"
            onKeyDown={e => { if (e.key === 'Enter' && text.trim()) onAnswer(text.trim()); }}
            autoFocus
          />
          <button
            onClick={() => text.trim() && onAnswer(text.trim())}
            disabled={!text.trim()}
            className="w-full border border-teal/40 text-teal py-3 rounded text-sm hover:bg-teal/10 transition-colors disabled:opacity-40"
          >
            Continue
          </button>
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function DonorConfirm() {
  const [params]   = useSearchParams();
  const request_id = params.get('request_id') || '';
  const donor_id   = params.get('donor_id')   || '';

  const [req,        setReq]        = useState(null);
  const [loadErr,    setLoadErr]    = useState(null);
  const [action,     setAction]     = useState(null);   // 'yes' | 'no'
  const [submitting, setSubmitting] = useState(false);
  const [visible,    setVisible]    = useState(false);

  // Follow-up questionnaire state
  const [step,        setStep]        = useState(0);   // 0 = not started, 1-4 = questions
  const [answers,     setAnswers]     = useState({});
  const [submitted,   setSubmitted]   = useState(false);
  const [donorStatus, setDonorStatus] = useState('DONOR_FOUND'); // tracks donor's own journey
  const [statusBusy,  setStatusBusy]  = useState(false);

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
      if (choice === 'yes') setStep(1);  // start questionnaire
    } catch (e) {
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  };

  const handleAnswer = (questionId, answer) => {
    const newAnswers = { ...answers, [questionId]: answer };
    setAnswers(newAnswers);

    // First question: if they say No, treat as decline
    if (questionId === 'can_donate' && answer.startsWith('No')) {
      setAction('no');
      setStep(0);
      return;
    }

    if (step < QUESTIONS.length) {
      setStep(step + 1);
    } else {
      // All questions answered — submit to backend
      submitAnswers(newAnswers);
    }
  };

  const submitAnswers = async (finalAnswers) => {
    try {
      await fetch(`${API}/donor/response`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          request_id,
          donor_id,
          answers: finalAnswers,
        }),
      });
    } catch (e) {
      console.error('Failed to submit donor answers:', e);
    } finally {
      setSubmitted(true);
    }
  };

  // ── Guards ─────────────────────────────────────────────────────────────────

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

  if (!req && !loadErr) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <span className="text-xs font-mono text-muted-fg animate-pulse">Loading request...</span>
      </div>
    );
  }

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

  const urgencyKey = (req?.urgency || '').toLowerCase();
  const urgency    = URGENCY_MAP[urgencyKey] || URGENCY_MAP.standard;

  // ── Expired: donor already declined this request ───────────────────────────
  const declinedIds = req?.declined_donor_ids || [];
  const alreadyDeclined = declinedIds.includes(donor_id);
  // Also expired if someone else already confirmed (DONOR_FOUND or beyond)
  const alreadyConfirmedByOther = req?.confirmed_donor_id && req.confirmed_donor_id !== donor_id
    && ['DONOR_FOUND','ARRIVING','TRANSFUSING','COMPLETED'].includes(req?.status);

  if ((alreadyDeclined || alreadyConfirmedByOther) && !action) {
    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
        <div className="fixed inset-0 grid-lines pointer-events-none opacity-20" />
        <div className={`relative z-10 max-w-sm w-full text-center transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'}`}>
          <div className="w-16 h-16 rounded-full bg-border/40 flex items-center justify-center mx-auto mb-6">
            <svg className="w-7 h-7 text-muted-fg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
          </div>
          <h1 className="font-display text-4xl text-foreground leading-tight mb-3">
            This link has expired.
          </h1>
          <p className="text-sm text-muted-fg leading-relaxed">
            {alreadyDeclined
              ? 'You have already responded to this request. Thank you for letting us know.'
              : 'A donor has already confirmed for this request. Thank you for your willingness to help.'}
          </p>
        </div>
      </div>
    );
  }

  // ── Questionnaire (after YES) ──────────────────────────────────────────────

  if (action === 'yes' && step >= 1 && step <= QUESTIONS.length && !submitted) {
    const q = QUESTIONS[step - 1];
    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
        <div className="fixed inset-0 grid-lines pointer-events-none opacity-20" />
        <div className={`relative z-10 max-w-sm w-full transition-all duration-500 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
          <div className="mb-6">
            <span className="inline-flex items-center gap-2 text-xs font-mono text-muted-fg mb-4">
              <span className="w-1.5 h-1.5 rounded-full bg-safe" />
              A patient needs your help
            </span>
            <div className="font-mono text-xs border border-border px-2 py-1 inline-block text-muted-fg mb-4">
              {req?.blood_group} · {req?.hospital_name}
            </div>
          </div>

          <div className="border border-border bg-card rounded p-6">
            <QuestionStep
              question={q}
              onAnswer={(ans) => handleAnswer(q.id, ans)}
              stepNum={step}
              totalSteps={QUESTIONS.length}
            />
          </div>
        </div>
      </div>
    );
  }

  // ── Donor journey (after questions answered) ──────────────────────────────

  const advanceDonorStatus = async (newStatus) => {
    setStatusBusy(true);
    try {
      await fetch(`${API}/request/${request_id}/status`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_status: newStatus }),
      });
      setDonorStatus(newStatus);
    } catch (e) { console.error(e); }
    finally { setStatusBusy(false); }
  };

  if (action === 'yes' && submitted) {
    const transport  = answers.transport || '';
    const needPickup = transport.includes('pickup');
    const isComplete = donorStatus === 'COMPLETED';

    // ── Success screen ─────────────────────────────────────────────────
    if (isComplete) {
      return (
        <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
          <div className="fixed inset-0 grid-lines pointer-events-none opacity-20" />
          <div className={`relative z-10 max-w-sm w-full text-center transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'}`}>
            <div className="w-20 h-20 rounded-full bg-safe/10 border-2 border-safe/40 flex items-center justify-center mx-auto mb-8">
              <svg className="w-10 h-10 text-safe" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
              </svg>
            </div>
            <h1 className="font-display text-5xl text-foreground leading-tight mb-4">
              You saved<br />a life.
            </h1>
            <p className="text-sm text-muted-fg leading-relaxed mb-6">
              Transfusion complete. Thank you for being a hero. Your donation at{' '}
              <strong className="text-foreground">{req?.hospital_name}</strong> has made a difference.
            </p>
            <div className="text-[10px] font-mono text-muted-fg/50">{request_id}</div>
          </div>
        </div>
      );
    }

    // ── Active donor journey screen ────────────────────────────────────
    const DONOR_STEPS = [
      { status: 'DONOR_FOUND',  label: 'Confirmed',    done: true },
      { status: 'ARRIVING',     label: 'On the way',   done: donorStatus === 'ARRIVING' || donorStatus === 'TRANSFUSING' },
      { status: 'TRANSFUSING',  label: 'Transfusing',  done: donorStatus === 'TRANSFUSING' },
      { status: 'COMPLETED',    label: 'Done',         done: false },
    ];

    const ACTION_BTN = {
      DONOR_FOUND:  { label: "I'm on my way to the hospital", next: 'ARRIVING',    color: 'border-teal/50 text-teal hover:bg-teal/10' },
      ARRIVING:     { label: 'Transfusion has started',       next: 'TRANSFUSING', color: 'border-orange-400/50 text-orange-400 hover:bg-orange-400/10' },
      TRANSFUSING:  { label: 'Transfusion complete ✓',        next: 'COMPLETED',   color: 'border-safe/50 text-safe hover:bg-safe/10' },
    };

    const btn = ACTION_BTN[donorStatus];

    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
        <div className="fixed inset-0 grid-lines pointer-events-none opacity-20" />
        <div className={`relative z-10 max-w-sm w-full transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'}`}>

          {/* Header */}
          <div className="mb-6">
            <span className="inline-flex items-center gap-2 text-xs font-mono text-muted-fg mb-3">
              <span className="w-1.5 h-1.5 rounded-full bg-safe animate-pulse" />
              You're confirmed — keep this page open
            </span>
            <h1 className="font-display text-4xl text-foreground leading-tight">
              You're saving<br />a life.
            </h1>
          </div>

          {/* Request info */}
          <div className="border border-border bg-card rounded divide-y divide-border mb-6">
            <Row label="Blood Group" value={req?.blood_group} />
            <Row label="Hospital"    value={req?.hospital_name} />
            <Row label="Urgency"     value={urgency.label} highlight={urgency.color} />
            <Row label="Your ETA"    value={answers.arrival_time} />
            {needPickup && <Row label="Transport" value="Pickup being arranged" />}
          </div>

          {/* Journey steps */}
          <div className="border border-border bg-card rounded p-5 mb-5">
            <div className="text-[10px] font-mono text-muted-fg uppercase tracking-widest mb-4">Your journey</div>
            <div className="space-y-3">
              {DONOR_STEPS.map(({ status, label, done }, i) => {
                const isActive = donorStatus === status;
                return (
                  <div key={status} className="flex items-center gap-3">
                    <div className={`w-5 h-5 rounded-full flex items-center justify-center shrink-0 ${
                      done    ? 'bg-safe'      :
                      isActive ? 'bg-teal animate-pulse' :
                                 'bg-border'
                    }`}>
                      {done && (
                        <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </div>
                    <span className={`text-sm ${done || isActive ? 'text-foreground font-medium' : 'text-muted-fg'}`}>
                      {label}
                    </span>
                    {isActive && <span className="text-[10px] font-mono text-teal ml-auto">current</span>}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Action button */}
          {btn && (
            <button
              onClick={() => advanceDonorStatus(btn.next)}
              disabled={statusBusy}
              className={`w-full border py-4 rounded font-medium text-sm active:scale-95 transition-all disabled:opacity-40 ${btn.color}`}
            >
              {statusBusy ? 'Updating…' : btn.label}
            </button>
          )}

          <p className="text-center text-[10px] font-mono text-muted-fg mt-4">{request_id}</p>
        </div>
      </div>
    );
  }

  // ── Declined ───────────────────────────────────────────────────────────────

  if (action === 'no') {
    return (
      <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
        <div className={`relative z-10 max-w-sm w-full text-center transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'}`}>
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-6">
            <span className="w-6 h-px bg-foreground/20" />
            Response recorded
          </span>
          <h1 className="font-display text-4xl text-foreground leading-tight mb-4">
            Thank you for<br />letting us know.
          </h1>
          <p className="text-sm text-muted-fg leading-relaxed">
            We'll contact another eligible donor immediately.
          </p>
        </div>
      </div>
    );
  }

  // ── Default: YES / NO prompt ───────────────────────────────────────────────

  return (
    <div className="min-h-screen flex items-center justify-center noise-overlay px-6">
      <div className="fixed inset-0 grid-lines pointer-events-none opacity-20" />

      <div className={`relative z-10 max-w-sm w-full transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'}`}>
        <div className="mb-8">
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-5">
            <span className="w-1.5 h-1.5 rounded-full bg-blood pulse-blood" />
            Blood donation request
          </span>
          <h1 className="font-display text-5xl text-foreground leading-tight tracking-tight">
            A patient<br />needs you.
          </h1>
        </div>

        {urgencyKey === 'critical' && (
          <div className="border border-danger/30 bg-danger/5 px-4 py-3 rounded mb-5 flex items-center gap-2 glow-blood">
            <span className="w-1.5 h-1.5 rounded-full bg-danger pulse-blood" />
            <span className="text-danger text-xs font-mono uppercase tracking-widest">
              Critical — every minute matters
            </span>
          </div>
        )}

        <div className="border border-border rounded divide-y divide-border mb-6 bg-card">
          <Row label="Blood Group" value={req?.blood_group} />
          <Row label="Hospital"    value={req?.hospital_name} />
          <Row label="Urgency"     value={urgency.label} highlight={urgency.color} />
          <Row label="Request ID"  value={request_id} mono />
        </div>

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
