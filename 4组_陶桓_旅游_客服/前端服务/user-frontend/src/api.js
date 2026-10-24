export const CUSTOMER_SERVICE_URL = import.meta.env.VITE_CUSTOMER_SERVICE_URL || 'http://127.0.0.1:8000'
export const TOURISM_SERVICE_URL = import.meta.env.VITE_TOURISM_SERVICE_URL || 'http://127.0.0.1:8001'

async function parseResponse(response) {
  const body = await response.json().catch(() => ({ detail: response.statusText }))
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : body.detail?.message
    throw new Error(detail || body.message || response.statusText)
  }
  return body
}

export async function login(userId) {
  return parseResponse(await fetch(`${TOURISM_SERVICE_URL}/api/v1/auth/demo-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, role: 'customer' }),
  }))
}

export async function listDemoUsers() {
  return parseResponse(await fetch(`${TOURISM_SERVICE_URL}/api/v1/demo/users`))
}

export async function apiFetch(baseUrl, path, token, options = {}) {
  const headers = { Authorization: `Bearer ${token}`, ...(options.headers || {}) }
  return parseResponse(await fetch(`${baseUrl}${path}`, { ...options, headers }))
}

export const customerApi = {
  history: (token, afterSequence = null) => apiFetch(
    CUSTOMER_SERVICE_URL,
    `/api/v1/chat/history${afterSequence == null ? '' : `?after_sequence=${afterSequence}`}`,
    token,
  ),
  currentConversation: (token) => apiFetch(CUSTOMER_SERVICE_URL, '/api/v1/conversations/current', token, { method: 'POST' }),
  send: (token, payload) => apiFetch(CUSTOMER_SERVICE_URL, '/api/v1/chat/messages', token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
}

export const tourismApi = {
  products: (token, query = '', type = '') => apiFetch(
    TOURISM_SERVICE_URL,
    `/api/v1/products?query=${encodeURIComponent(query)}${type ? `&type=${encodeURIComponent(type)}` : ''}`,
    token,
  ),
  product: (token, id) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/products/${encodeURIComponent(id)}`, token),
  availability: (token, id) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/products/${encodeURIComponent(id)}/availability`, token),
  orders: (token) => apiFetch(TOURISM_SERVICE_URL, '/api/v1/orders', token),
  order: (token, id) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}`, token),
  trip: (token, id) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/trip`, token),
  refunds: (token, orderId = '') => apiFetch(
    TOURISM_SERVICE_URL,
    `/api/v1/refunds${orderId ? `?order_id=${encodeURIComponent(orderId)}` : ''}`,
    token,
  ),
  tickets: (token, orderId = '') => apiFetch(
    TOURISM_SERVICE_URL,
    `/api/v1/tickets${orderId ? `?order_id=${encodeURIComponent(orderId)}` : ''}`,
    token,
  ),
  cancel: (token, id) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/cancel`, token, {
    method: 'POST',
    headers: { 'Idempotency-Key': newId('cancel') },
  }),
  updateTravelers: (token, id, travelerInfo) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/travelers`, token, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': newId('travelers') },
    body: JSON.stringify({ traveler_info: travelerInfo }),
  }),
  createRefund: (token, id, payload) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/refunds`, token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': newId('refund') },
    body: JSON.stringify(payload),
  }),
  createTicket: (token, id, payload) => apiFetch(TOURISM_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/tickets`, token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': newId('ticket') },
    body: JSON.stringify(payload),
  }),
}

export function createRealtimeConnection(token, callbacks = {}) {
  let socket
  let stopped = false
  let retryTimer
  let attempts = 0
  let hasConnected = false
  const websocketUrl = `${CUSTOMER_SERVICE_URL.replace(/^http/, 'ws')}/api/v1/realtime`

  const clearTimers = () => {
    window.clearTimeout(retryTimer)
  }
  const connect = () => {
    if (stopped) return
    callbacks.onState?.(attempts ? 'reconnecting' : 'connecting')
    socket = new WebSocket(websocketUrl)
    socket.addEventListener('open', () => socket.send(JSON.stringify({ type: 'authenticate', token })))
    socket.addEventListener('message', (browserEvent) => {
      const event = JSON.parse(browserEvent.data)
      if (event.event_type === 'connected') {
        const reconnected = hasConnected
        hasConnected = true
        attempts = 0
        callbacks.onState?.('connected')
        callbacks.onConnected?.({ reconnected })
      } else {
        callbacks.onEvent?.(event)
      }
    })
    socket.addEventListener('close', () => {
      clearTimers()
      if (stopped) return callbacks.onState?.('closed')
      callbacks.onState?.('reconnecting')
      retryTimer = window.setTimeout(connect, Math.min(1000 * (2 ** attempts++), 10000))
    })
    socket.addEventListener('error', () => socket?.close())
  }
  connect()
  return { close() { stopped = true; clearTimers(); socket?.close(1000, 'client closed') } }
}

export function newId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`
}
