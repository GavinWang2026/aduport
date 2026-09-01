/** Lightweight toast — no Ant Design dependency. */
type Kind = 'info' | 'success' | 'warning' | 'error'

function show(kind: Kind, text: string, ms = 2800) {
  let host = document.getElementById('md-toast-host')
  if (!host) {
    host = document.createElement('div')
    host.id = 'md-toast-host'
    host.setAttribute(
      'style',
      'position:fixed;bottom:28px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none',
    )
    document.body.appendChild(host)
  }
  const el = document.createElement('div')
  const colors: Record<Kind, string> = {
    info: 'var(--brand)',
    success: 'var(--ok)',
    warning: 'var(--warn)',
    error: 'var(--bad)',
  }
  el.textContent = text
  el.setAttribute(
    'style',
    [
      'pointer-events:auto',
      'padding:11px 17px',
      'border-radius:var(--r-lg)',
      'background:var(--bg-card-solid)',
      'color:var(--t-hi)',
      'border:1px solid var(--line-strong)',
      `box-shadow:var(--shadow-2),0 0 0 1px ${colors[kind]}22`,
      'font:500 12.5px/1.4 var(--sans)',
      'animation:tin var(--dur) var(--ease)',
      'max-width:min(90vw,420px)',
    ].join(';'),
  )
  host.appendChild(el)
  window.setTimeout(() => {
    el.remove()
    if (host && !host.childElementCount) host.remove()
  }, ms)
}

export const message = {
  info: (t: string) => show('info', t),
  success: (t: string) => show('success', t),
  warning: (t: string) => show('warning', t),
  error: (t: string) => show('error', t),
}
