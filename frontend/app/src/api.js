const BASE = '/api'

function getToken()        { return localStorage.getItem('scenarai_token') }
function getRefreshToken() { return localStorage.getItem('scenarai_refresh_token') }
function storeTokens(data) {
  localStorage.setItem('scenarai_token', data.access_token)
  localStorage.setItem('scenarai_refresh_token', data.refresh_token)
}
function clearTokens() {
  localStorage.removeItem('scenarai_token')
  localStorage.removeItem('scenarai_refresh_token')
}
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

// Access tokens are short-lived (30min, see backend/config.py) by design —
// a stolen token that can't be individually revoked should only be
// dangerous for a bounded window. That tradeoff only works if expiry is
// invisible to the user, so a 401 triggers one silent refresh-and-retry
// before it's treated as a real logout.
//
// Shared in-flight promise, not a fresh refresh call per request: refresh
// tokens are single-use (rotated on every exchange, see
// backend/refresh_tokens.py), so if two requests 401 at once and both
// call /auth/refresh independently, only the first succeeds — the second
// would get "invalid token" using an already-rotated token. Coalescing
// concurrent 401s onto one shared refresh avoids that self-inflicted race.
let _refreshInFlight = null

async function _doRefresh() {
  const refreshToken = getRefreshToken()
  if (!refreshToken) throw new Error('No refresh token')
  const res = await fetch(`${BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
  if (!res.ok) {
    clearTokens()
    throw new Error('Session expired — please log in again')
  }
  const data = await res.json()
  storeTokens(data)
  return data
}

function refreshTokens() {
  if (!_refreshInFlight) {
    _refreshInFlight = _doRefresh().finally(() => { _refreshInFlight = null })
  }
  return _refreshInFlight
}

async function request(method, path, body, _isRetry = false) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && !_isRetry && getRefreshToken()) {
    await refreshTokens()
    return request(method, path, body, true)
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  // Auth
  register:       (email, password)                => request('POST',   '/auth/register', { email, password }),
  login:          (email, password)                => request('POST',   '/auth/login',    { email, password }),
  me:             ()                               => request('GET',    '/auth/me'),
  changePassword: (current_password, new_password) => request('POST',   '/auth/change-password', { current_password, new_password }),
  deleteAccount:  ()                               => request('DELETE', '/auth/me'),
  // Revokes server-side so the refresh token can't mint new access
  // tokens after logout — doesn't route through request()'s 401-retry
  // logic (logging out is the one call that should never try to refresh).
  logout: () => {
    const refreshToken = getRefreshToken()
    clearTokens()
    if (!refreshToken) return Promise.resolve()
    return fetch(`${BASE}/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch(() => {}) // best-effort — local tokens are already cleared either way
  },
  storeTokens,

  // Personas
  createPersona: (data) => request('POST',   '/personas',     data),
  listPersonas:  (cursor, limit = 20) => request('GET', `/personas?limit=${limit}${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`),
  updatePersona: (id, data) => request('PUT', `/personas/${id}`, data),
  deletePersona: (id)        => request('DELETE', `/personas/${id}`),

  // Scenarios
  createScenario: (data) => request('POST', '/scenarios', data),
  listScenarios: (cursor, limit = 20) => request('GET', `/scenarios?limit=${limit}${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`),
  listPublicScenarios: (cursor, limit = 20) => request('GET', `/scenarios/public?limit=${limit}${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`),
  getScenario:    (id)   => request('GET',    `/scenarios/${id}`),
  updateScenario:   (id, data) => request('PUT',  `/scenarios/${id}`, data),
  publishScenario:   (id)       => request('POST',   `/scenarios/${id}/publish`),
  unpublishScenario: (id)       => request('POST',   `/scenarios/${id}/unpublish`),
  deleteScenario:    (id)       => request('DELETE', `/scenarios/${id}`),
  saveScenario:      (id)       => request('POST',   `/scenarios/${id}/save`),
  unsaveScenario:    (id)       => request('DELETE', `/scenarios/${id}/save`),
  reportScenario:    (id, reason) => request('POST', `/scenarios/${id}/report`, { reason }),
  getLastSession:    (id)       => request('GET',    `/scenarios/${id}/last-session`),

  // Sessions
  createSession:     (scenario_id, persona_id, content_filter = 'off', preview = false) =>
                     request('POST', '/sessions', { scenario_id, persona_id, content_filter, preview }),
  sessionStatus:     (session_id) =>
                     request('GET',  `/sessions/${session_id}/status`),
  playTurn:          (session_id, input, engine_model) =>
                     request('POST',  `/sessions/${session_id}/turn`, { input, engine_model }),
  continueTurn:      (session_id, engine_model) =>
                     request('POST',  `/sessions/${session_id}/continue`, { engine_model }),
  regenerateTurn:    (session_id, engine_model) =>
                     request('POST',  `/sessions/${session_id}/regenerate`, { engine_model }),
  rateTurn:          (session_id, turn, rating) =>
                     request('POST',  `/sessions/${session_id}/turns/${turn}/rating`, { rating }),
  getTurnMedia:      (session_id) =>
                     request('GET',  `/sessions/${session_id}/media`),
  regenerateTurnMedia: (session_id, turn) =>
                     request('POST',  `/sessions/${session_id}/turns/${turn}/regenerate-media`),
  exportSessionVideo: (session_id, from_turn, to_turn) =>
                     request('POST',  `/sessions/${session_id}/export`, { from_turn, to_turn }),
  // Billing is mock-only until a real payment processor exists — see
  // backend/models.py's UserSubscription docstring. These 404 unless the
  // backend's MOCK_BILLING_ENABLED flag is on.
  listTiers:         () => request('GET',  '/billing/tiers'),
  getMySubscription: () => request('GET',  '/billing/subscription'),
  mockSubscribe:     (tier_id) => request('POST', `/billing/subscribe/${tier_id}`),
  getTelemetry:      () => request('GET', '/admin/telemetry'),
  playTurnStream:    (session_id, input, engine_model) => {
    // Returns a Response for SSE streaming
    const token = getToken()
    return fetch(`${BASE}/sessions/${session_id}/turn-stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ input, engine_model }),
    }).then(res => {
      if (!res.ok) throw new Error('Stream request failed')
      return res
    })
  },
  endSession:        (session_id) =>
                     request('DELETE', `/sessions/${session_id}`),
  sessionHistory:    (cursor, limit = 20) => request('GET', `/sessions/history?limit=${limit}${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`),
  getSessionHistory: (session_id) => request('GET', `/sessions/${session_id}/history`),

  // Models
  listModels: () => request('GET', '/models'),

  // Poll until engine ready — checks every 3s, gives up after 10 minutes
  waitForEngine: (session_id) => new Promise((resolve, reject) => {
    const start    = Date.now()
    const TIMEOUT  = 10 * 60 * 1000  // 10 minutes
    const INTERVAL = 3000
    const check = async () => {
      try {
        const s = await request('GET', `/sessions/${session_id}/status`)
        if (s.status === 'ready') return resolve(s)
        if (s.status === 'error') return reject(new Error('Engine initialization failed'))
        if (Date.now() - start > TIMEOUT) return reject(new Error('Engine init timed out'))
        setTimeout(check, INTERVAL)
      } catch (err) {
        reject(err)
      }
    }
    setTimeout(check, INTERVAL)
  }),
}
