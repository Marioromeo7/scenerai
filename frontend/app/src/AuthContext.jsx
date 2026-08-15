import { createContext, useContext, useState, useEffect } from 'react'
import { api } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('scenarai_token')
    if (token) {
      // api.me() transparently refreshes on a stale access token (see
      // api.js's request() interceptor) -- reaching .catch() here means
      // the refresh token itself was also invalid/expired/revoked, so
      // both stored tokens are stale, not just the access token.
      api.me()
        .then(setUser)
        .catch(() => {
          localStorage.removeItem('scenarai_token')
          localStorage.removeItem('scenarai_refresh_token')
        })
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  async function login(email, password) {
    const data = await api.login(email, password)
    api.storeTokens(data)
    setUser(data.user)
    return data.user
  }

  async function register(email, password) {
    const data = await api.register(email, password)
    api.storeTokens(data)
    setUser(data.user)
    return data.user
  }

  async function logout() {
    await api.logout() // revokes the refresh token server-side, then clears local storage either way
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() { return useContext(AuthContext) }
