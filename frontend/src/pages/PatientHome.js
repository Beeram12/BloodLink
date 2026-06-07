import React, { useState, useRef, useEffect, useCallback } from 'react';
import { API } from '../App';

const USER_ERROR = "We are experiencing a brief technical issue. Your request is important to us. Please try sending your message again in a moment.";

const INITIAL_MESSAGE = {
  role:    'assistant',
  content: "Hello, welcome to BloodLink. It is truly wonderful that you are here — every person who reaches out helps save a life. Are you looking to donate blood, or do you need blood for a patient?",
};

const GREETING_OPTIONS = [
  { label: 'I want to donate blood',      color: 'border-teal/50 bg-teal/5 text-teal hover:bg-teal/10' },
  { label: 'I need blood for a patient',  color: 'border-blood/40 bg-blood/5 text-blood hover:bg-red-50' },
];

const BLOOD_GROUP_OPTIONS = [
  { label: 'O+',  value: 'O Positive',  color: 'border-blood/30 text-blood   hover:bg-red-50' },
  { label: 'O−',  value: 'O Negative',  color: 'border-blood/30 text-blood   hover:bg-red-50' },
  { label: 'A+',  value: 'A Positive',  color: 'border-border   text-foreground hover:border-blood/40 hover:text-blood' },
  { label: 'A−',  value: 'A Negative',  color: 'border-border   text-foreground hover:border-blood/40 hover:text-blood' },
  { label: 'B+',  value: 'B Positive',  color: 'border-border   text-foreground hover:border-blood/40 hover:text-blood' },
  { label: 'B−',  value: 'B Negative',  color: 'border-border   text-foreground hover:border-blood/40 hover:text-blood' },
  { label: 'AB+', value: 'AB Positive', color: 'border-border   text-foreground hover:border-blood/40 hover:text-blood' },
  { label: 'AB−', value: 'AB Negative', color: 'border-border   text-foreground hover:border-blood/40 hover:text-blood' },
];

const URGENCY_OPTIONS = [
  { label: 'Critical — needed within hours',  value: 'Critical', color: 'border-danger/50 text-danger   bg-danger/[0.02]  hover:bg-danger/5' },
  { label: 'Urgent — needed within 24 hours', value: 'Urgent',   color: 'border-orange-400/50 text-orange-500 hover:bg-orange-50' },
  { label: 'Standard — needed within 3 days', value: 'Standard', color: 'border-warn/50   text-warn     hover:bg-warn/5' },
];

function detectButtonMode(text) {
  if (text.includes('##SHOW_URGENCY_BUTTONS##')) return 'urgency';
  if (text.includes('##SHOW_BLOOD_BUTTONS##'))   return 'bloodgroup';
  const t = text.toLowerCase();
  if (
    (t.includes('critical') && t.includes('urgent') && t.includes('standard')) ||
    t.includes('how urgently')
  ) return 'urgency';
  if (t.includes('blood group') && (t.includes('o positive') || t.includes('ab positive')))
    return 'bloodgroup';
  if (t.includes('donate blood') || t.includes('need blood') || t.includes('wonderful that you'))
    return 'greeting';
  return null;
}

function Bubble({ role, content }) {
  const isAI = role === 'assistant';
  const display = content
    .replace(/REQUEST_READY\|[^\n]*/g, '')
    .replace(/##SHOW_URGENCY_BUTTONS##/g, '')
    .replace(/##SHOW_BLOOD_BUTTONS##/g, '')
    .trim();
  return (
    <div className={`flex ${isAI ? 'justify-start' : 'justify-end'} mb-4`}>
      {isAI && (
        <div className="w-9 h-9 rounded-full bg-blood flex items-center justify-center mr-3 mt-0.5 flex-shrink-0 shadow-sm">
          <span className="text-white text-xs font-bold font-mono">BL</span>
        </div>
      )}
      <div className={`max-w-[80%] px-5 py-3.5 text-base leading-relaxed shadow-sm ${
        isAI
          ? 'bg-white border border-border text-foreground rounded-2xl rounded-tl-none'
          : 'bg-blood text-white rounded-2xl rounded-tr-none'
      }`}>
        {display}
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
          <span key={i} className="w-2 h-2 bg-muted-fg rounded-full animate-bounce"
            style={{ animationDelay: `${i * 150}ms` }} />
        ))}
      </div>
    </div>
  );
}

function OptionButtons({ options, onSelect, disabled }) {
  return (
    <div className="flex flex-col gap-2 mt-1 mb-3">
      {options.map(opt => (
        <button
          key={opt.label}
          disabled={disabled}
          onClick={() => onSelect(opt.value || opt.label)}
          className={`text-sm font-medium border px-4 py-3 rounded-xl text-left transition-all active:scale-[0.99] disabled:opacity-40 ${opt.color}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function BloodGroupButtons({ onSelect, disabled }) {
  return (
    <div className="mt-1 mb-3">
      <p className="text-xs font-mono text-muted-fg mb-2">Select blood group:</p>
      <div className="grid grid-cols-4 gap-2">
        {BLOOD_GROUP_OPTIONS.map(opt => (
          <button
            key={opt.label}
            disabled={disabled}
            onClick={() => onSelect(opt.value)}
            className={`text-sm font-bold border px-3 py-2.5 rounded-xl transition-all active:scale-95 disabled:opacity-40 ${opt.color}`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Mic button ────────────────────────────────────────────────────────────────
// Uses continuous=false (one utterance at a time). Shows interim text live in
// input. On final result, places text in input — user taps Send to submit.
// Tap mic again to record another utterance and append to existing text.
function MicButton({ onInterim, onFinal, disabled }) {
  const [isListening, setIsListening] = useState(false);
  const [supported,   setSupported]   = useState(false);
  const recRef = useRef(null);

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    setSupported(!!SR);
  }, []);

  const start = useCallback(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR || isListening) return;

    const rec = new SR();
    rec.lang            = 'en-US';
    rec.continuous      = false;
    rec.interimResults  = true;
    rec.maxAlternatives = 1;
    recRef.current = rec;

    rec.onstart = () => setIsListening(true);

    rec.onresult = (e) => {
      let text = '';
      for (let i = 0; i < e.results.length; i++) {
        text += e.results[i][0].transcript;
      }
      // Always push whatever we hear into the input field
      onInterim(text);
    };

    rec.onerror = () => setIsListening(false);

    rec.onend = () => {
      setIsListening(false);
      onFinal();
    };

    try {
      rec.start();
    } catch (e) {
      setIsListening(false);
    }
  }, [isListening, onInterim, onFinal]);

  const stop = useCallback(() => {
    recRef.current?.stop();
    recRef.current = null;
    setIsListening(false);
  }, []);

  if (!supported) return null;

  return (
    <button
      type="button"
      aria-label={isListening ? 'Stop listening' : 'Start voice input'}
      tabIndex={0}
      disabled={disabled}
      onClick={isListening ? stop : start}
      className={`px-4 py-4 flex items-center justify-center transition-all disabled:opacity-30 ${
        isListening ? 'text-danger animate-pulse' : 'text-muted-fg hover:text-foreground'
      }`}
    >
      {isListening ? (
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
          <path d="M12 15a3 3 0 003-3V6a3 3 0 10-6 0v6a3 3 0 003 3z"/>
          <path d="M19 11a1 1 0 00-2 0 5 5 0 01-10 0 1 1 0 00-2 0 7 7 0 006 6.93V20H9a1 1 0 000 2h6a1 1 0 000-2h-2v-2.07A7 7 0 0019 11z"/>
        </svg>
      ) : (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z"/>
        </svg>
      )}
    </button>
  );
}

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

function HowItWorks() {
  const steps = [
    { n: '01', title: 'Share the patient ID',  desc: 'Tell our AI the patient user ID and how urgent the need is.' },
    { n: '02', title: 'AI finds matches',       desc: 'We score 7,000+ donors instantly by blood type, distance, and history.' },
    { n: '03', title: 'WhatsApp outreach',      desc: 'Top donors get a personal WhatsApp message within seconds.' },
    { n: '04', title: 'Confirmed fast',         desc: 'First donor to say yes — you see it live. Usually under 4 minutes.' },
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
        {steps.map(step => (
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

// ── Main ──────────────────────────────────────────────────────────────────────
export default function PatientHome() {
  const [messages,     setMessages]     = useState([INITIAL_MESSAGE]);
  const [input,        setInput]        = useState('');
  const [loading,      setLoading]      = useState(false);
  const [connected,    setConnected]    = useState(true);
  const [requestId,    setRequestId]    = useState(null);
  const [visible,      setVisible]      = useState(false);
  const [buttonMode,   setButtonMode]   = useState('greeting');
  const [reqStatus,    setReqStatus]    = useState(null);   // live request status
  const [donorFound,   setDonorFound]   = useState(null);   // timestamp when DONOR_FOUND
  const [elapsed,      setElapsed]      = useState('');     // e.g. "2m 14s"
  const chatBoxRef   = useRef(null);
  const inputRef     = useRef(null);
  const pollRef      = useRef(null);
  const timerRef     = useRef(null);

  useEffect(() => { setVisible(true); }, []);
  useEffect(() => {
    if (chatBoxRef.current)
      chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight;
  }, [messages, loading]);

  // Poll request status when a request exists
  useEffect(() => {
    if (!requestId) return;
    const poll = async () => {
      try {
        const res  = await fetch(`${API}/request/${requestId}`);
        if (!res.ok) return;
        const data = await res.json();
        const status = data.status;
        setReqStatus(status);

        // Find DONOR_FOUND timestamp from history
        if (status === 'DONOR_FOUND' || status === 'ARRIVING' || status === 'TRANSFUSING' || status === 'COMPLETED') {
          const history = data.status_history || [];
          const found   = history.find(h => h.status === 'DONOR_FOUND');
          if (found && !donorFound) setDonorFound(new Date(found.timestamp));
        }
      } catch (_) {}
    };
    poll();
    pollRef.current = setInterval(poll, 5000);
    return () => clearInterval(pollRef.current);
  }, [requestId]); // eslint-disable-line

  // Elapsed time ticker once donor found
  useEffect(() => {
    if (!donorFound) return;
    const tick = () => {
      const secs = Math.floor((Date.now() - donorFound.getTime()) / 1000);
      const m = Math.floor(secs / 60), s = secs % 60;
      setElapsed(`${m}m ${s}s`);
    };
    tick();
    timerRef.current = setInterval(tick, 1000);
    return () => clearInterval(timerRef.current);
  }, [donorFound]);

  const reset = () => {
    clearInterval(pollRef.current);
    clearInterval(timerRef.current);
    setMessages([INITIAL_MESSAGE]);
    setInput('');
    setRequestId(null);
    setButtonMode('greeting');
    setLoading(false);
    setConnected(true);
    setReqStatus(null);
    setDonorFound(null);
    setElapsed('');
  };

  const send = useCallback(async (overrideText) => {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;

    setButtonMode(null);
    const userMsg    = { role: 'user', content: text };
    const newHistory = [...messages, userMsg];
    setMessages(newHistory);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch(`${API}/chat`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ messages: newHistory }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setConnected(true);

      const data         = await res.json();
      const responseText = data.response_text || USER_ERROR;

      setMessages(prev => [...prev, { role: 'assistant', content: responseText }]);
      if (data.request_id) setRequestId(data.request_id);
      setButtonMode(detectButtonMode(responseText));

    } catch (err) {
      console.warn('[BloodLink] API error:', err?.message || err);
      setConnected(false);
      setMessages(prev => [...prev, { role: 'assistant', content: USER_ERROR }]);
      setButtonMode(null);
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [input, loading, messages]);

  const dotColor = connected ? 'bg-safe animate-pulse' : 'bg-danger';

  return (
    <div className="noise-overlay bg-background min-h-screen">
      <div className="fixed inset-0 grid-lines pointer-events-none opacity-60 z-0" />

      {/* Hero */}
      <div className="relative z-10 pt-24 pb-8 text-center px-4">
        <div className={`transition-all duration-700 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
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
            Our AI coordinator will guide you. We contact eligible donors via WhatsApp instantly.
          </p>
        </div>
      </div>

      {/* Chat */}
      <div className={`relative z-10 max-w-2xl mx-auto px-4 pb-6 transition-all duration-700 delay-200 ${
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'
      }`}>

        {requestId && (
          <div className={`mb-4 border bg-white rounded-xl p-5 shadow-sm transition-colors duration-500 ${
            reqStatus === 'DONOR_FOUND' || reqStatus === 'ARRIVING' || reqStatus === 'TRANSFUSING' || reqStatus === 'COMPLETED'
              ? 'border-safe' : 'border-blue-200'
          }`}>
            <div className="flex items-start gap-3">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 ${
                reqStatus === 'DONOR_FOUND' || reqStatus === 'ARRIVING' || reqStatus === 'TRANSFUSING' || reqStatus === 'COMPLETED'
                  ? 'bg-safe' : 'bg-blue-500 animate-pulse'
              }`}>
                <span className="text-white text-sm font-bold">
                  {reqStatus === 'DONOR_FOUND' || reqStatus === 'ARRIVING' || reqStatus === 'TRANSFUSING' || reqStatus === 'COMPLETED' ? '✓' : '→'}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-mono text-sm font-bold text-foreground tracking-wider">{requestId}</p>
                {/* Status line */}
                {!reqStatus || reqStatus === 'SEARCHING' ? (
                  <p className="text-blue-600 text-sm mt-1 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse inline-block" />
                    Searching for donors nearby…
                  </p>
                ) : reqStatus === 'DONOR_FOUND' ? (
                  <div className="mt-1">
                    <p className="text-safe font-semibold text-sm flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-safe animate-pulse inline-block" />
                      Donor confirmed! They are on the way.
                    </p>
                    {elapsed && <p className="text-xs font-mono text-muted-fg mt-0.5">Time since confirmed: {elapsed}</p>}
                  </div>
                ) : reqStatus === 'ARRIVING' ? (
                  <div className="mt-1">
                    <p className="text-teal font-semibold text-sm">Donor is arriving at the hospital.</p>
                    {elapsed && <p className="text-xs font-mono text-muted-fg mt-0.5">Time elapsed: {elapsed}</p>}
                  </div>
                ) : reqStatus === 'TRANSFUSING' ? (
                  <p className="text-orange-500 font-semibold text-sm mt-1">Transfusion in progress.</p>
                ) : reqStatus === 'COMPLETED' ? (
                  <p className="text-safe font-bold text-sm mt-1">✓ Transfusion complete. Thank you!</p>
                ) : null}
              </div>
            </div>
          </div>
        )}

        <div className="bg-white border border-border rounded-2xl shadow-lg overflow-hidden">

          {/* Toolbar */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-muted/40">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${dotColor}`} />
              <span className="text-sm font-mono text-muted-fg">BloodLink Coordinator</span>
            </div>
            <span className="text-xs font-mono text-muted-fg bg-muted px-2 py-0.5 rounded">AI</span>
          </div>

          {/* Messages */}
          <div ref={chatBoxRef} className="h-80 lg:h-96 overflow-y-auto px-5 pt-5 pb-2">
            {messages.map((m, i) => <Bubble key={i} role={m.role} content={m.content} />)}
            {loading && <TypingDots />}
          </div>

          {/* Contextual buttons */}
          {!loading && !requestId && buttonMode && (
            <div className="px-5 pt-2">
              {buttonMode === 'greeting'   && <OptionButtons options={GREETING_OPTIONS} onSelect={send} disabled={loading} />}
              {buttonMode === 'bloodgroup' && <BloodGroupButtons onSelect={send} disabled={loading} />}
              {buttonMode === 'urgency'    && <OptionButtons options={URGENCY_OPTIONS}  onSelect={send} disabled={loading} />}
            </div>
          )}

          {/* Input bar */}
          <div className="border-t border-border">
            <div className="flex items-center min-h-[56px]">
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), send())}
                disabled={loading}
                placeholder="Type or tap mic…"
                className="flex-1 min-w-0 bg-transparent text-foreground text-base px-4 py-3 placeholder-muted-fg focus:outline-none disabled:opacity-40"
              />
              <MicButton
                disabled={loading}
                onInterim={text => setInput(text)}
                onFinal={(text, err) => { if (err) console.warn('[Mic]', err); }}
              />
              <button
                onClick={() => send()}
                disabled={loading || !input.trim()}
                className="bg-blood text-white font-semibold px-5 py-3 m-1.5 rounded-xl hover:bg-blood/90 active:scale-95 transition-all disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-1.5 shrink-0 text-sm"
              >
                {loading ? (
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                  </svg>
                ) : (
                  <>
                    <span>Send</span>
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                    </svg>
                  </>
                )}
              </button>
              {requestId && (
                <button onClick={reset}
                  className="text-muted-fg text-xs font-mono px-3 py-3 hover:text-foreground transition-colors border-l border-border shrink-0">
                  End
                </button>
              )}
            </div>
            {!input && !loading && (
              <p className="text-[10px] text-muted-fg font-mono text-center pb-1.5">
                Tap mic to speak in Hindi or English
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="relative z-10 mt-4"><StatsBar /></div>
      <div className="relative z-10"><HowItWorks /></div>
      <footer className="relative z-10 border-t border-border py-8 text-center">
        <p className="text-xs font-mono text-muted-fg">BloodLink · AI Donor Matching · Available 24/7</p>
      </footer>
    </div>
  );
}
