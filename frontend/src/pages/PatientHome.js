import React, { useState, useRef, useEffect } from 'react';
import { API } from '../App';

const INITIAL_MESSAGE = {
  role:    'assistant',
  content: "Hi, I'm BloodLink. I can help you find a blood donor. Could you share the patient's User ID or blood group to get started?",
};

// ── Chat bubble ──────────────────────────────────────────────────────────────
function Bubble({ role, content }) {
  const isAI = role === 'assistant';
  return (
    <div className={`flex ${isAI ? 'justify-start' : 'justify-end'} mb-4`}>
      {isAI && (
        <div className="w-9 h-9 rounded-full bg-blood flex items-center justify-center mr-3 mt-0.5 flex-shrink-0 shadow-sm">
          <span className="text-white text-xs font-bold font-mono">BL</span>
        </div>
      )}
      <div
        className={`max-w-[80%] px-5 py-3.5 text-base leading-relaxed shadow-sm ${
          isAI
            ? 'bg-white border border-border text-foreground rounded-2xl rounded-tl-none'
            : 'bg-blood text-white rounded-2xl rounded-tr-none'
        }`}
      >
        {content}
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <div className="flex justify-start mb-4">
      <div className="w-9 h-9 rounded-full bg-blood flex items-center justify-center mr-3 mt-0.5 flex-shrink-0">
        <span className="text-white text-xs font-bold font-mono">BL</span>
      </div>
      <div className="bg-white border border-border px-5 py-4 rounded-2xl rounded-tl-none flex gap-1.5 items-center shadow-sm">
        {[0,1,2].map(i => (
          <span key={i}
            className="w-2 h-2 bg-muted-fg rounded-full animate-bounce"
            style={{ animationDelay: `${i * 150}ms` }} />
        ))}
      </div>
    </div>
  );
}

// ── Blood group quick-select chips ───────────────────────────────────────────
function BloodChips({ onSelect, disabled }) {
  const groups = ['O+', 'O−', 'A+', 'A−', 'B+', 'B−', 'AB+', 'AB−'];
  const labels = {
    'O+': 'O Positive', 'O−': 'O Negative',
    'A+': 'A Positive', 'A−': 'A Negative',
    'B+': 'B Positive', 'B−': 'B Negative',
    'AB+': 'AB Positive', 'AB−': 'AB Negative',
  };
  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {groups.map(g => (
        <button
          key={g}
          disabled={disabled}
          onClick={() => onSelect(labels[g])}
          className="text-sm font-mono border border-border bg-white px-3 py-1.5 rounded-full hover:border-blood hover:text-blood hover:bg-red-50 transition-colors disabled:opacity-40 shadow-sm"
        >
          {g}
        </button>
      ))}
    </div>
  );
}

// ── Stats bar ────────────────────────────────────────────────────────────────
function StatsBar() {
  const stats = [
    { value: '< 4 min', label: 'avg donor contact' },
    { value: '94%',     label: 'match rate' },
    { value: '7,033',   label: 'active donors' },
    { value: '24 / 7',  label: 'AI online' },
  ];
  return (
    <div className="overflow-hidden border-t border-border py-4 bg-muted">
      <div className="flex gap-16 marquee whitespace-nowrap">
        {[0,1].map(pass => (
          <div key={pass} className="flex gap-16 items-center">
            {stats.map(s => (
              <div key={`${s.label}-${pass}`} className="flex items-baseline gap-2.5">
                <span className="font-display text-3xl text-foreground">{s.value}</span>
                <span className="text-xs text-muted-fg font-mono">{s.label}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── How it works ─────────────────────────────────────────────────────────────
function HowItWorks() {
  const steps = [
    { n: '01', title: 'Describe the need',   desc: 'Tell our AI the blood group, hospital name, and how urgent it is.' },
    { n: '02', title: 'AI finds matches',    desc: 'We score 7,000+ donors instantly by blood type, distance, and history.' },
    { n: '03', title: 'WhatsApp outreach',   desc: 'Top donors get a personal WhatsApp message within seconds.' },
    { n: '04', title: 'Confirmed fast',      desc: 'First donor to say yes — you see it live. Usually under 4 minutes.' },
  ];
  return (
    <section className="py-20 max-w-3xl mx-auto px-6">
      <div className="mb-12 text-center">
        <span className="text-xs font-mono text-muted-fg uppercase tracking-widest">How it works</span>
        <h2 className="font-display text-4xl lg:text-5xl text-foreground mt-3 leading-tight">
          Simple. Fast. Reliable.
        </h2>
      </div>
      <div>
        {steps.map((step) => (
          <div key={step.n} className="flex gap-8 py-8 border-b border-border group hover-lift">
            <span className="font-mono text-sm text-muted-fg pt-1 w-8 shrink-0">{step.n}</span>
            <div>
              <h3 className="font-sans font-semibold text-xl text-foreground mb-1.5 group-hover:text-blood transition-colors duration-300">
                {step.title}
              </h3>
              <p className="text-muted-fg leading-relaxed">{step.desc}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────
export default function PatientHome() {
  const [messages,  setMessages]  = useState([INITIAL_MESSAGE]);
  const [input,     setInput]     = useState('');
  const [loading,   setLoading]   = useState(false);
  const [requestId, setRequestId] = useState(null);
  const [visible,   setVisible]   = useState(false);
  const [showChips, setShowChips] = useState(true);
  const bottomRef  = useRef(null);
  const inputRef   = useRef(null);
  const chatBoxRef = useRef(null);

  useEffect(() => { setVisible(true); }, []);
  useEffect(() => {
    if (chatBoxRef.current) {
      chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const reset = () => {
    setMessages([INITIAL_MESSAGE]);
    setInput('');
    setRequestId(null);
    setShowChips(true);
    setLoading(false);
  };

  const send = async (overrideText) => {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;
    setShowChips(false);
    const userMsg    = { role: 'user', content: text };
    const newHistory = [...messages, userMsg];
    setMessages(newHistory);
    setInput('');
    setLoading(true);

    try {
      const res  = await fetch(`${API}/chat`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ messages: newHistory }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      setMessages(prev => [...prev, { role: 'assistant', content: data.response_text }]);
      if (data.request_id) setRequestId(data.request_id);
    } catch {
      setMessages(prev => [...prev, {
        role:    'assistant',
        content: "I'm having trouble connecting right now. Please try again.",
      }]);
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  return (
    <div className="noise-overlay bg-background min-h-screen">
      {/* Subtle grid */}
      <div className="fixed inset-0 grid-lines pointer-events-none opacity-60 z-0" />

      {/* ── Hero header ─────────────────────────────────────────────── */}
      <div className="relative z-10 pt-24 pb-8 text-center px-4">
        <div
          className={`transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
        >
          <span className="inline-flex items-center gap-2 text-xs font-mono text-muted-fg mb-4">
            <span className="w-5 h-px bg-foreground/20" />
            AI-powered blood donor matching
            <span className="w-5 h-px bg-foreground/20" />
          </span>
          <h1 className="font-display text-5xl lg:text-7xl text-foreground leading-[1] tracking-tight mb-3">
            Find a donor.<br />
            <span className="text-blood">Save a life.</span>
          </h1>
          <p className="text-muted-fg text-lg max-w-md mx-auto mt-4 leading-relaxed">
            Describe what the patient needs below. We contact eligible donors via WhatsApp instantly.
          </p>
        </div>
      </div>

      {/* ── Chat — large, centered, dominant ────────────────────────── */}
      <div
        className={`relative z-10 max-w-2xl mx-auto px-4 pb-6 transition-all duration-700 delay-200 ${
          visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'
        }`}
      >
        {/* Request created banner */}
        {requestId && (
          <div className="mb-4 border border-safe bg-white rounded-xl p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-full bg-safe flex items-center justify-center flex-shrink-0 mt-0.5">
                <span className="text-white text-sm font-bold">✓</span>
              </div>
              <div>
                <p className="font-semibold text-safe text-base">Request created successfully</p>
                <p className="font-mono text-xl font-bold text-foreground mt-1 tracking-wider">
                  {requestId}
                </p>
                <p className="text-muted-fg text-sm mt-1">
                  Contacting eligible donors via WhatsApp now.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Chat card */}
        <div className="bg-white border border-border rounded-2xl shadow-lg overflow-hidden">
          {/* Toolbar */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-muted/40">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-blood pulse-blood" />
              <span className="text-sm font-mono text-muted-fg">BloodLink Coordinator</span>
            </div>
            <span className="text-xs font-mono text-muted-fg bg-muted px-2 py-0.5 rounded">AI</span>
          </div>

          {/* Messages — tall, readable */}
          <div ref={chatBoxRef} className="h-80 lg:h-96 overflow-y-auto px-5 pt-5 pb-2">
            {messages.map((m, i) => <Bubble key={i} role={m.role} content={m.content} />)}
            {loading && <TypingDots />}
          </div>

          {/* Blood group chips — shown on first message only */}
          {showChips && (
            <div className="px-5 pb-3">
              <p className="text-xs text-muted-fg font-mono mb-1">Quick select blood group:</p>
              <BloodChips onSelect={(v) => send(v)} disabled={loading || !!requestId} />
            </div>
          )}

          {/* Input bar */}
          <div className="border-t border-border flex items-center gap-0">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), send())}
              disabled={loading}
              placeholder={requestId ? 'Ask a follow-up question...' : 'Type your message here...'}
              className="flex-1 bg-transparent text-foreground text-base px-5 py-4 placeholder-muted-fg focus:outline-none disabled:opacity-40"
            />
            <button
              onClick={() => send()}
              disabled={loading || !input.trim()}
              className="bg-blood text-white text-sm font-semibold px-6 py-4 hover:bg-blood/90 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Send
            </button>
            {requestId && (
              <button
                onClick={reset}
                className="text-muted-fg text-sm font-mono px-4 py-4 hover:text-foreground transition-colors border-l border-border"
                title="Start a new request"
              >
                End
              </button>
            )}
          </div>
        </div>

        {/* Help tip */}
        <p className="text-center text-xs text-muted-fg font-mono mt-3">
          {requestId
            ? 'Donors are being contacted · Click End to start a new request'
            : 'Tell us the blood group · hospital name · urgency level'}
        </p>
      </div>

      {/* ── Stats marquee ───────────────────────────────────────────── */}
      <div className="relative z-10 mt-4">
        <StatsBar />
      </div>

      {/* ── How it works ────────────────────────────────────────────── */}
      <div className="relative z-10">
        <HowItWorks />
      </div>

      {/* Footer */}
      <footer className="relative z-10 border-t border-border py-8 text-center">
        <p className="text-xs font-mono text-muted-fg">
          BloodLink · AI Donor Matching · Available 24/7
        </p>
      </footer>
    </div>
  );
}
