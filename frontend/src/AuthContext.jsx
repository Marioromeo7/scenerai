import { createContext, useContext, useState, useEffect } from 'react'
import { api } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('scenarai_token')
    if (token) {
      api.me()
        .then(setUser)
        .catch(() => localStorage.removeItem('scenarai_token'))
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  async function login(email, password) {
    const data = await api.login(email, password)
    localStorage.setItem('scenarai_token', data.access_token)
    setUser(data.user)
    return data.user
  }

  async function register(email, password) {
    const data = await api.register(email, password)
    localStorage.setItem('scenarai_token', data.access_token)
    setUser(data.user)
    return data.user
  }

  function logout() {
    localStorage.removeItem('scenarai_token')
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() { return useContext(AuthContext) }
