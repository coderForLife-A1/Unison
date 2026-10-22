import React, { useState, useEffect } from 'react';
import { 
  Bell, 
  ShieldCheck, 
  Zap, 
  RotateCw, 
  Trash2, 
  Smartphone, 
  Send, 
  Sparkles,
  Inbox,
  Filter,
  AlertCircle
} from 'lucide-react';

const API_BASE = window.location.port === '5173' ? '' : 'http://127.0.0.1:8000';

export default function App() {
  const [status, setStatus] = useState({
    ollama_online: false,
    ollama_model: '...',
    total_summaries: 0,
    high_priority_count: 0,
    action_required_count: 0,
    noise_count: 0
  });

  const [summaries, setSummaries] = useState([]);
  const [source, setSource] = useState('mock');
  const [slothMode, setSlothMode] = useState(false);
  const [selectedPriority, setSelectedPriority] = useState('ALL');
  const [loading, setLoading] = useState(false);
  const [showManualSend, setShowManualSend] = useState(false);

  // Manual Notification Test State (for simulating Android incoming notifications)
  const [manualForm, setManualForm] = useState({
    source: 'WhatsApp',
    sender: 'Project Lead',
    content: 'Can you review the release checklist before 5 PM today?'
  });

  // Fetch initial dashboard status and summaries
  const fetchData = async () => {
    try {
      const [resStatus, resSummaries] = await Promise.all([
        fetch(`${API_BASE}/api/status`),
        fetch(`${API_BASE}/api/summaries`)
      ]);
      if (resStatus.ok) {
        setStatus(await resStatus.json());
      }
      if (resSummaries.ok) {
        setSummaries(await resSummaries.json());
      }
    } catch (err) {
      console.warn("Backend connection pending:", err);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 8000);
    return () => clearInterval(interval);
  }, []);

  // Ingest batch via configured adapter
  const handleIngest = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source })
      });
      if (res.ok) {
        await fetchData();
      }
    } catch (err) {
      alert("Failed to ingest: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  // Push direct notification (like an Android NotificationListener would)
  const handleDirectPush = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/notifications/direct`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(manualForm)
      });
      if (res.ok) {
        setShowManualSend(false);
        await fetchData();
      }
    } catch (err) {
      alert("Push failed: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  // Clear feed
  const handleClear = async () => {
    try {
      await fetch(`${API_BASE}/api/summaries`, { method: 'DELETE' });
      await fetchData();
    } catch (err) {
      console.error(err);
    }
  };

  // Filter items
  const filteredSummaries = summaries.filter(item => {
    if (slothMode && !item.action_required) return false;
    if (selectedPriority !== 'ALL' && item.priority !== selectedPriority) return false;
    return true;
  });

  return (
    <div className="app-container">
      {/* ── Sidebar for Desktop & Config for Mobile ── */}
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon">🦥</span>
          <div>
            <h1 className="brand-title">Unison</h1>
            <p className="brand-caption">Slow & Steady Local AI</p>
          </div>
        </div>

        {/* Ollama Status Pill */}
        <div className={`status-pill ${status.ollama_online ? 'status-online' : 'status-offline'}`}>
          <span className={`dot ${status.ollama_online ? 'dot-online' : 'dot-offline'}`} />
          <span>
            {status.ollama_online ? `Ollama Active (${status.ollama_model})` : 'Ollama Offline'}
          </span>
        </div>

        {!status.ollama_online && (
          <div style={{ fontSize: '0.8rem', color: '#f87171', background: 'rgba(239, 68, 68, 0.1)', padding: '10px', borderRadius: '8px', border: '1px solid rgba(239, 68, 68, 0.2)' }}>
            <AlertCircle size={14} style={{ verticalAlign: 'middle', marginRight: '4px' }} />
            Run <code>ollama serve</code> in terminal to enable local AI summaries.
          </div>
        )}

        {/* Sloth Mode Switch */}
        <div className="toggle-card" onClick={() => setSlothMode(!slothMode)}>
          <div className="toggle-info">
            <span className="toggle-title">🦥 Sloth Mode</span>
            <span className="toggle-desc">Show actionable items only</span>
          </div>
          <label className="switch" onClick={e => e.stopPropagation()}>
            <input 
              type="checkbox" 
              checked={slothMode} 
              onChange={e => setSlothMode(e.target.checked)} 
            />
            <span className="slider" />
          </label>
        </div>

        {/* Ingestion Source Selector */}
        <div className="control-group">
          <label className="control-label">Ingestion Source</label>
          <select value={source} onChange={e => setSource(e.target.value)}>
            <option value="mock">Simulated Stream (Demo)</option>
            <option value="file">Local File Watcher (data/inbox)</option>
            <option value="email">IMAP Mailbox</option>
          </select>
        </div>

        {/* Action Buttons */}
        <button 
          className="btn btn-primary" 
          onClick={handleIngest} 
          disabled={loading}
        >
          <RotateCw size={16} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Processing Batch...' : 'Ingest & Unison'}
        </button>

        <button 
          className="btn btn-secondary"
          onClick={() => setShowManualSend(!showManualSend)}
        >
          <Smartphone size={16} />
          {showManualSend ? 'Close Mobile Simulator' : 'Simulate Mobile Alert'}
        </button>

        <button className="btn btn-secondary" onClick={handleClear} style={{ marginTop: 'auto' }}>
          <Trash2 size={16} />
          Clear All Digests
        </button>
      </aside>

      {/* ── Main Dashboard Feed ── */}
      <main className="main-content">
        <div style={{ marginBottom: '20px' }}>
          <h2 style={{ fontSize: '1.6rem', fontWeight: '700', letterSpacing: '-0.02em' }}>
            Unified Notification Intelligence
          </h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            All alerts processed locally. Semantic deduplication & structured triage on device.
          </p>
        </div>

        {/* Mobile / Direct Test Input Box */}
        {showManualSend && (
          <form className="manual-box" onSubmit={handleDirectPush}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <Smartphone size={18} color="var(--accent-cyan)" />
              <strong style={{ fontSize: '0.95rem' }}>Push Direct Alert (Android Notification Bridge)</strong>
            </div>
            <div className="manual-grid">
              <select 
                value={manualForm.source} 
                onChange={e => setManualForm({ ...manualForm, source: e.target.value })}
              >
                <option value="WhatsApp">WhatsApp</option>
                <option value="SMS">SMS</option>
                <option value="Email">Email</option>
                <option value="System">System</option>
              </select>
              <input 
                type="text" 
                placeholder="Sender name / number" 
                value={manualForm.sender} 
                onChange={e => setManualForm({ ...manualForm, sender: e.target.value })}
                required
              />
            </div>
            <input 
              type="text" 
              placeholder="Notification content payload..." 
              style={{ width: '100%', marginBottom: '12px' }}
              value={manualForm.content} 
              onChange={e => setManualForm({ ...manualForm, content: e.target.value })}
              required
            />
            <button className="btn btn-primary" type="submit" disabled={loading} style={{ width: 'auto' }}>
              <Send size={15} /> Push Notification
            </button>
          </form>
        )}

        {/* Metrics Grid */}
        <div className="metrics-grid">
          <div className="metric-card">
            <span className="metric-num">{status.total_summaries}</span>
            <span className="metric-label">Total Digests</span>
          </div>
          <div className="metric-card">
            <span className="metric-num" style={{ color: 'var(--accent-red)' }}>{status.high_priority_count}</span>
            <span className="metric-label">High Priority</span>
          </div>
          <div className="metric-card">
            <span className="metric-num" style={{ color: 'var(--accent-green)' }}>{status.action_required_count}</span>
            <span className="metric-label">Actions Required</span>
          </div>
          <div className="metric-card">
            <span className="metric-num" style={{ color: 'var(--text-muted)' }}>{status.noise_count}</span>
            <span className="metric-label">Noise Filtered</span>
          </div>
        </div>

        {/* Priority Filter Chips */}
        <div className="chips-container">
          {['ALL', 'High', 'Low', 'Noise'].map(p => (
            <button
              key={p}
              className={`chip ${selectedPriority === p ? 'active' : ''}`}
              onClick={() => setSelectedPriority(p)}
            >
              {p === 'ALL' ? 'All Alerts' : p}
            </button>
          ))}
        </div>

        {/* Notification Digest List */}
        <div className="digest-list">
          {filteredSummaries.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)' }}>
              <Inbox size={42} style={{ marginBottom: '12px', opacity: 0.6 }} />
              <p style={{ fontWeight: '500' }}>No notifications in digest.</p>
              <p style={{ fontSize: '0.85rem' }}>Click "Ingest & Unison" or send an alert to populate.</p>
            </div>
          ) : (
            filteredSummaries.map((item, idx) => {
              const tagClass = 
                item.priority === 'High' ? 'tag-high' :
                item.priority === 'Low' ? 'tag-low' : 'tag-noise';

              return (
                <div key={item.batch_id || idx} className="card">
                  <div className="card-header">
                    <div className="badges-group">
                      <span className={`tag ${tagClass}`}>{item.priority}</span>
                      <span className="tag tag-noise">{item.category}</span>
                      {item.action_required && (
                        <span className="tag tag-action">⚡ Action Required</span>
                      )}
                    </div>
                    <span className="card-source">{item.source}</span>
                  </div>

                  <div className="card-summary">
                    {item.summary}
                  </div>

                  {item.suggested_reply && (
                    <div className="reply-container">
                      <div className="reply-header">
                        <Sparkles size={14} />
                        <span>Suggested Quick Reply:</span>
                      </div>
                      <div className="reply-text">"{item.suggested_reply}"</div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </main>
    </div>
  );
}
