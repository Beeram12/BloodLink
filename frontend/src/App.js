import React, { createContext, useContext, useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation, Navigate } from 'react-router-dom';
import PatientHome  from './pages/PatientHome';
import Pipeline     from './pages/Pipeline';
import DonorConfirm from './pages/DonorConfirm';

export const API = (process.env.REACT_APP_API_URL || '').replace(/\/$/, '');

// ── Warrior auth context ────────────────────────────────────────────────────
// Warriors authenticate with a PIN stored in REACT_APP_WARRIOR_PIN env var.
// Falls back to "bloodlink" for local dev / demo.

const WARRIOR_PIN = process.env.REACT_APP_WARRIOR_PIN || 'bloodlink';
const AUTH_KEY    = 'bl_warrior_auth';

const AuthContext = createContext(null);

function AuthProvider({ children }) {
  const [authenticated, setAuthenticated] = useState(
    () => sessionStorage.getItem(AUTH_KEY) === '1'
  );

  const login  = (pin) => {
    if (pin === WARRIOR_PIN) {
      sessionStorage.setItem(AUTH_KEY, '1');
      setAuthenticated(true);
      return true;
    }
    return false;
  };

  const logout = () => {
    sessionStorage.removeItem(AUTH_KEY);
    setAuthenticated(false);
  };

  return (
    <AuthContext.Provider value={{ authenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

// ── Warrior login gate ──────────────────────────────────────────────────────

function WarriorLogin({ onSuccess }) {
  const [pin, setPin]     = useState('');
  const [error, setError] = useState(false);
  const { login }         = useAuth();

  const attempt = (e) => {
    e.preventDefault();
    if (login(pin)) {
      onSuccess?.();
    } else {
      setError(true);
      setPin('');
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center noise-overlay">
      {/* Subtle grid */}
      <div className="absolute inset-0 grid-lines pointer-events-none opacity-50" />

      <div className="relative z-10 w-full max-w-sm px-6">
        {/* Header */}
        <div className="mb-10">
          <span className="inline-flex items-center gap-3 text-xs font-mono text-muted-fg mb-6">
            <span className="w-6 h-px bg-foreground/20" />
            Restricted access
          </span>
          <h1 className="font-display text-5xl text-foreground leading-tight">
            Warrior<br />Portal
          </h1>
          <p className="text-muted-fg text-sm mt-3 leading-relaxed">
            Enter your access PIN to view the operations pipeline.
          </p>
        </div>

        <form onSubmit={attempt} className="space-y-4">
          <div>
            <input
              type="password"
              value={pin}
              onChange={e => { setPin(e.target.value); setError(false); }}
              placeholder="Access PIN"
              autoFocus
              className={`w-full bg-muted border text-foreground px-4 py-3 font-mono text-sm rounded focus:outline-none focus:border-teal transition-colors placeholder-muted-fg ${
                error ? 'border-danger' : 'border-border'
              }`}
            />
            {error && (
              <p className="text-danger text-xs font-mono mt-1.5">Incorrect PIN. Try again.</p>
            )}
          </div>
          <button
            type="submit"
            className="w-full bg-foreground text-background font-medium py-3 rounded hover:bg-foreground/90 transition-colors text-sm"
          >
            Authenticate
          </button>
        </form>

        <div className="mt-8 pt-6 border-t border-border">
          <Link
            to="/"
            className="text-xs text-muted-fg hover:text-foreground transition-colors font-mono flex items-center gap-2"
          >
            <span>←</span> Back to patient portal
          </Link>
        </div>
      </div>
    </div>
  );
}

// ── Protected route ─────────────────────────────────────────────────────────

function WarriorRoute({ children }) {
  const { authenticated }     = useAuth();
  const [showLogin, setShowLogin] = useState(!authenticated);

  if (authenticated) return children;
  if (showLogin) return <WarriorLogin onSuccess={() => setShowLogin(false)} />;
  return <Navigate to="/" replace />;
}

// ── Navigation ──────────────────────────────────────────────────────────────

function Nav() {
  const { pathname }    = useLocation();
  const { authenticated, logout } = useAuth();
  const [scrolled, setScrolled]   = useState(false);

  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 20);
    window.addEventListener('scroll', h);
    return () => window.removeEventListener('scroll', h);
  }, []);

  const isConfirm = pathname === '/confirm';
  if (isConfirm) return null;

  return (
    <header
      className={`fixed z-50 transition-all duration-500 ${
        scrolled ? 'top-3 left-3 right-3' : 'top-0 left-0 right-0'
      }`}
    >
      <nav
        className={`mx-auto transition-all duration-500 ${
          scrolled
            ? 'bg-background/80 backdrop-blur-xl border border-foreground/10 rounded-xl shadow-xl max-w-5xl'
            : 'bg-transparent max-w-6xl'
        }`}
      >
        <div className={`flex items-center justify-between px-6 transition-all duration-500 ${scrolled ? 'h-12' : 'h-16'}`}>
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2 group">
            <span className="w-1.5 h-1.5 rounded-full bg-blood pulse-blood" />
            <span className="font-display text-xl text-foreground tracking-tight">
              Blood<span className="text-blood">Link</span>
            </span>
            <span className="text-muted-fg font-mono text-[9px] mt-1">AI</span>
          </Link>

          {/* Right side */}
          <div className="flex items-center gap-6">
            {/* Patient nav */}
            {pathname === '/' && (
              <span className="text-xs font-mono text-muted-fg flex items-center gap-1.5">
                <span className="w-1 h-1 rounded-full bg-safe" />
                Patient Portal
              </span>
            )}

            {/* Pipeline link — always visible if authenticated */}
            {authenticated ? (
              <>
                <Link
                  to="/pipeline"
                  className={`text-sm transition-colors relative group ${
                    pathname === '/pipeline' ? 'text-foreground' : 'text-muted-fg hover:text-foreground'
                  }`}
                >
                  Pipeline
                  <span className="absolute -bottom-0.5 left-0 w-0 h-px bg-foreground transition-all duration-300 group-hover:w-full" />
                </Link>
                <button
                  onClick={logout}
                  className="text-xs font-mono text-muted-fg hover:text-danger transition-colors"
                >
                  Sign out
                </button>
              </>
            ) : (
              <Link
                to="/pipeline"
                className="text-sm text-muted-fg hover:text-foreground transition-colors relative group"
              >
                Warriors
                <span className="absolute -bottom-0.5 left-0 w-0 h-px bg-foreground transition-all duration-300 group-hover:w-full" />
              </Link>
            )}
          </div>
        </div>
      </nav>
    </header>
  );
}

// ── Root ────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <div className="min-h-screen bg-background text-foreground">
          <Nav />
          <Routes>
            <Route path="/"        element={<PatientHome />} />
            <Route path="/confirm" element={<DonorConfirm />} />
            <Route
              path="/pipeline"
              element={
                <WarriorRoute>
                  <Pipeline />
                </WarriorRoute>
              }
            />
          </Routes>
        </div>
      </BrowserRouter>
    </AuthProvider>
  );
}
