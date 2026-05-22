import { useState, useEffect, useCallback } from 'react'

// The API lives on port 9600 of the same host
const API = `http://${window.location.hostname}:9600`

const STATE_COLORS = {
  idle:     '#64748b',
  playing:  '#22c55e',
  stopped:  '#ef4444',
  error:    '#ef4444',
}

async function api(path, method = 'GET', body = undefined) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(`${API}${path}`, opts)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

export default function App() {
  const [files, setFiles]           = useState([])
  const [selected, setSelected]     = useState('')
  const [status, setStatus]         = useState(null)
  const [error, setError]           = useState('')
  const [loading, setLoading]       = useState(false)

  const clearError = () => setError('')

  // Poll status every 2 s
  const fetchStatus = useCallback(async () => {
    try {
      const s = await api('/status')
      setStatus(s)
    } catch {
      // backend not yet ready – ignore
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 2000)
    return () => clearInterval(id)
  }, [fetchStatus])

  // Load file list once on mount
  useEffect(() => {
    api('/files')
      .then(d => {
        setFiles(d.files || [])
        if (d.files?.length) setSelected(d.files[0])
      })
      .catch(() => {})
  }, [])

  const cmd = async (path, body) => {
    setLoading(true)
    clearError()
    try {
      await api(path, 'POST', body)
      await fetchStatus()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const stateColor = STATUS_COLORS[status?.state] ?? '#64748b'
  const currentState = status?.state ?? 'unknown'

  return (
    <div style={{ width: '100%', maxWidth: 640 }}>
      <h1 style={{ fontSize: '1.5rem', marginBottom: '1.5rem', letterSpacing: '0.05em' }}>
        🎬 MXL File Player
      </h1>

      {/* Status badge */}
      <div style={styles.card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
          <span style={{ ...styles.badge, background: stateColor }}>
            {currentState.toUpperCase()}
          </span>
          {status?.file && (
            <span style={{ fontSize: '0.85rem', color: '#94a3b8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {status.file.split('/').pop()}
            </span>
          )}
        </div>
        {status?.video_flow_id && (
          <div style={styles.flowInfo}>
            <FlowRow label="Video flow" id={status.video_flow_id} />
            <FlowRow label="Audio flow" id={status.audio_flow_id} />
            <FlowRow label="MXL domain" id={status.mxl_domain} />
          </div>
        )}
      </div>

      {/* File selector */}
      <div style={styles.card}>
        <label style={styles.label}>Media file</label>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {files.length > 0 ? (
            <select
              value={selected}
              onChange={e => setSelected(e.target.value)}
              style={styles.select}
            >
              {files.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          ) : (
            <input
              value={selected}
              onChange={e => setSelected(e.target.value)}
              placeholder="filename.mp4"
              style={styles.input}
            />
          )}
          <Btn onClick={() => cmd('/load', { filename: selected })} disabled={!selected || loading} color="#3b82f6">
            Load
          </Btn>
        </div>
      </div>

      {/* Transport controls */}
      <div style={styles.card}>
        <label style={styles.label}>Transport</label>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <Btn onClick={() => cmd('/play')}  disabled={loading} color="#22c55e">▶ Play</Btn>
          <Btn onClick={() => cmd('/stop')}  disabled={loading} color="#ef4444">⏹ Stop</Btn>
        </div>
      </div>

      {error && (
        <div style={styles.errorBox}>
          ⚠ {error}
          <button onClick={clearError} style={styles.closeBtn}>✕</button>
        </div>
      )}
    </div>
  )
}

// Fix typo: alias STATE_COLORS as STATUS_COLORS for runtime usage
const STATUS_COLORS = STATE_COLORS

function FlowRow({ label, id }) {
  return (
    <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.75rem', color: '#94a3b8' }}>
      <span style={{ minWidth: 90, color: '#64748b' }}>{label}:</span>
      <span style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{id}</span>
    </div>
  )
}

function Btn({ onClick, disabled, color, children }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: '0.5rem 1.25rem',
        borderRadius: '0.375rem',
        border: 'none',
        background: disabled ? '#334155' : color,
        color: disabled ? '#64748b' : '#fff',
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontWeight: 600,
        fontSize: '0.9rem',
        transition: 'opacity 0.15s',
      }}
    >
      {children}
    </button>
  )
}

const styles = {
  card: {
    background: '#1e2535',
    border: '1px solid #2d3748',
    borderRadius: '0.5rem',
    padding: '1rem 1.25rem',
    marginBottom: '1rem',
  },
  label: {
    display: 'block',
    fontSize: '0.75rem',
    fontWeight: 700,
    letterSpacing: '0.1em',
    color: '#64748b',
    textTransform: 'uppercase',
    marginBottom: '0.5rem',
  },
  badge: {
    display: 'inline-block',
    padding: '0.2rem 0.6rem',
    borderRadius: '9999px',
    fontSize: '0.7rem',
    fontWeight: 700,
    letterSpacing: '0.08em',
    color: '#fff',
  },
  flowInfo: {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.2rem',
    marginTop: '0.5rem',
  },
  select: {
    flex: 1,
    background: '#0f1117',
    border: '1px solid #2d3748',
    borderRadius: '0.375rem',
    color: '#e2e8f0',
    padding: '0.45rem 0.75rem',
    fontSize: '0.9rem',
  },
  input: {
    flex: 1,
    background: '#0f1117',
    border: '1px solid #2d3748',
    borderRadius: '0.375rem',
    color: '#e2e8f0',
    padding: '0.45rem 0.75rem',
    fontSize: '0.9rem',
  },
  errorBox: {
    background: '#450a0a',
    border: '1px solid #ef4444',
    borderRadius: '0.5rem',
    padding: '0.75rem 1rem',
    fontSize: '0.85rem',
    color: '#fca5a5',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: '#fca5a5',
    cursor: 'pointer',
    fontSize: '1rem',
  },
}
