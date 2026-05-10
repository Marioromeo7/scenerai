const BASE = '/api'

function getToken() { return localStorage.getItem('scenarai_token') }
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  })
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
