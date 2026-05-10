import { useState, useEffect, useRef, Component } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { useAuth } from './AuthContext'

// ── Error Boundary ────────────────────────────────────────────
class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null } }
  static getDerivedStateFromError(error) { return { hasError: true, error } }
  componentDidCatch(error, info) { console.error('ErrorBoundary:', error, info) }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',padding:32,textAlign:'center'}}>
          <div>
            <div style={{fontSize:20,color:'var(--danger)',marginBottom:12}}>Something went wrong</div>
            <div style={{fontSize:13,color:'var(--text-muted)',marginBottom:20}}>{this.state.error?.message}</div>
            <button className="btn btn-primary" style={{width:'auto',padding:'8px 20px'}} onClick={() => this.setState({hasError:false,error:null})}>
              Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

const TAG_CLASS = {
  drama:'drama', power:'power', grief:'grief', betrayal:'betrayal',
  romance:'romance', thriller:'thriller', tension:'thriller',
  obsession:'betrayal', manipulation:'power', revenge:'drama',
  loyalty:'grief', corruption:'betrayal', desire:'romance',
  loss:'grief', control:'power',
}

function init(name='') { return (name||'??')[0].toUpperCase() }

// ── Card image placeholder ────────────────────────────────────
function CardImage({ seed, charName }) {
  const palettes = [['#1a0e08','#0d0d0f'],['#08101a','#0d0d0f'],['#100a1a','#0d0d0f'],['#0a1a10','#0d0d0f']]
  const [c1,c2] = palettes[(seed?.charCodeAt(0)||0)%palettes.length]
  const id = `g${seed||'x'}`
  return (
    <svg viewBox="0 0 280 140" xmlns="http://www.w3.org/2000/svg" style={{width:'100%',height:'100%'}}>
      <defs><linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stopColor={c1}/><stop offset="100%" stopColor={c2}/>
      </linearGradient></defs>
      <rect width="280" height="140" fill={`url(#${id})`}/>
      <text x="24" y="80" fontFamily="Georgia,serif" fontSize="28" fontStyle="italic" fill="rgba(201,169,110,0.15)">{init(charName)}</text>
      <rect x="24" y="90" width="40" height="1" fill="rgba(201,169,110,0.3)"/>
    </svg>
  )
}

// ── Scenario card with edit/delete/publish menu ───────────────
function ScenarioCard({ scenario, onPlay, saved, onSave, onEdit, onDelete, onPublish, isOwner }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const isDraft    = scenario.is_published === false
  const isBuilding = !isDraft && scenario.prefab_ready === false
  const isReady    = !isDraft && !isBuilding

  useEffect(() => {
    if (!menuOpen) return
    const close = () => setMenuOpen(false)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [menuOpen])

  const overlayContent = isDraft
    ? <>
        <div style={{fontSize:11,color:'var(--accent)',fontWeight:600,letterSpacing:'.06em',textTransform:'uppercase'}}>Draft</div>
        <div style={{fontSize:10,color:'var(--text-muted)'}}>Not published yet</div>
      </>
    : <>
        <div style={{fontSize:16,animation:'pulse 1.4s ease-in-out infinite'}}>⚙</div>
        <div style={{fontSize:11,color:'var(--text-secondary)',fontWeight:600,letterSpacing:'.06em',textTransform:'uppercase'}}>Under Construction</div>
        <div style={{fontSize:10,color:'var(--text-muted)'}}>Preparing scene engine…</div>
      </>

  return (
    <div className="scenario-card" style={{position:'relative'}}>
      <div onClick={() => (isReady || isDraft) && onPlay(scenario)} style={{cursor:(isReady||isDraft)?'pointer':'default'}}>
        <div className="card-image" style={{position:'relative'}}>
          <CardImage seed={scenario.image_seed} charName={scenario.char_name}/>
          {isReady
            ? <div className="card-play">
                <svg viewBox="0 0 16 16" width="14" height="14"><path d="M4 2l10 6-10 6V2z" fill="#1a1409"/></svg>
              </div>
            : <div style={{position:'absolute',inset:0,background:'rgba(13,13,15,0.82)',
                display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:6}}>
                {overlayContent}
              </div>
          }
        </div>
        <div className="card-body" style={{opacity:isReady||isDraft?1:0.5}}>
          <div className="card-title">{scenario.title||scenario.char_name}</div>
          <div className="card-brief">{scenario.brief||scenario.char_title}</div>
          <div className="card-tags">
            {(scenario.tags||[]).map(t=><span key={t} className={`tag ${TAG_CLASS[t]||''}`}>{t}</span>)}
          </div>
        </div>
      </div>
      <div className="card-footer">
        <div className="card-persona">
          <div className="card-persona-dot">{init(scenario.char_name)}</div>
          <div className="card-persona-name">{scenario.plays_count || 0} plays</div>
        </div>
        <div style={{display:'flex',alignItems:'center',gap:10}}>
          <div className="card-intensity">
            {[1,2,3,4,5].map(i=><div key={i} className={`dot ${i<=(scenario.intensity||3)?'lit':''}`}/>)}
          </div>
          {!isDraft && <button onClick={e=>{e.stopPropagation();onSave(scenario.id,saved)}}
            style={{background:'none',border:'none',cursor:'pointer',fontSize:16,
              color:saved?'var(--accent)':'var(--text-muted)',transition:'color .2s'}}
          >{saved?'★':'☆'}</button>}
        </div>
      </div>
      {isOwner && (
        <>
          <button onClick={e=>{e.stopPropagation();setMenuOpen(!menuOpen)}}
            style={{position:'absolute',top:8,right:8,zIndex:5,background:'var(--bg-card)',
              border:'1px solid var(--border)',borderRadius:6,width:28,height:28,
              display:'flex',alignItems:'center',justifyContent:'center',
              color:'var(--text-muted)',cursor:'pointer',fontSize:14}}>
            ⋯
          </button>
          {menuOpen && (
            <div className="card-actions open" style={{top:40,right:8}}>
              {isDraft && <button className="card-action-btn" onClick={e=>{e.stopPropagation();setMenuOpen(false);onEdit(scenario)}}>Edit</button>}
              {isDraft && <button className="card-action-btn" onClick={e=>{e.stopPropagation();setMenuOpen(false);onPublish(scenario.id)}}
                style={{color:'var(--accent)'}}>Publish</button>}
              <button className="card-action-btn danger" onClick={e=>{e.stopPropagation();setMenuOpen(false);onDelete(scenario.id)}}>Delete</button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Auth gate ─────────────────────────────────────────────────
function AuthGate() {
  const {login,register}=useAuth()
  const [m,setM]=useState('login')
  const [email,setEmail]=useState('')
  const [pw,setPw]=useState('')
  const [err,setErr]=useState('')
  const [loading,setLoading]=useState(false)
  async function submit(e) {
    e.preventDefault();setErr('');setLoading(true)
    try{m==='login'?await login(email,pw):await register(email,pw)}
    catch(e){setErr(e.message)}finally{setLoading(false)}
  }
  return (
    <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh'}}>
      <div className="auth-card" style={{width:360,background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:14,padding:'32px 28px'}}>
        <div style={{fontFamily:'var(--font-display)',fontSize:28,fontWeight:500,marginBottom:4}}>
          Scen<em style={{color:'var(--accent)'}}>ar</em>ai
        </div>
        <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:24}}>
          {m==='login'?'Sign in to continue':'Create your account'}
        </div>
        <form onSubmit={submit}>
          <div className="field"><label>Email</label>
            <input type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com" required/>
          </div>
          <div className="field"><label>Password</label>
            <input type="password" value={pw} onChange={e=>setPw(e.target.value)} placeholder="••••••••" required/>
          </div>
          {err&&<div style={{fontSize:13,color:'var(--danger)',marginBottom:12}}>{err}</div>}
          <button className="btn btn-primary" type="submit" disabled={loading}>
            {loading?'Please wait…':m==='login'?'Sign in':'Create account'}
          </button>
        </form>
        <div style={{marginTop:14,textAlign:'center',fontSize:13,color:'var(--text-muted)'}}>
          {m==='login'
            ?<><span>No account? </span><span style={{color:'var(--accent)',cursor:'pointer'}} onClick={()=>setM('register')}>Register</span></>
            :<><span>Have an account? </span><span style={{color:'var(--accent)',cursor:'pointer'}} onClick={()=>setM('login')}>Sign in</span></>}
        </div>
      </div>
    </div>
  )
}

// ── Engine init loading screen ────────────────────────────────
function InitializingScreen({ charName }) {
  const steps = [
    'Reading the opening scene…',
    'Building world state…',
    'Inferring character psychology…',
    'Generating behavioral constraints…',
    'Pinning scene facts to memory…',
    'Assembling context layers…',
    'Almost ready…',
  ]
  const [step, setStep] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setStep(s => (s+1) % steps.length), 2800)
    return () => clearInterval(t)
  }, [])
  return (
    <div style={{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
      height:'100%',gap:24,padding:48}}>
      <div style={{
        width:56,height:56,borderRadius:'50%',
        background:'var(--accent-dim)',border:'1px solid var(--accent)',
        display:'flex',alignItems:'center',justifyContent:'center',
        fontFamily:'var(--font-display)',fontSize:22,fontStyle:'italic',color:'var(--accent)',
      }}>{init(charName)}</div>
      <div style={{textAlign:'center'}}>
        <div style={{fontFamily:'var(--font-display)',fontSize:20,color:'var(--text-primary)',marginBottom:8}}>
          Initializing scene
        </div>
        <div style={{fontSize:13,color:'var(--text-muted)',marginBottom:24}}>
          The engine is building the world — this takes about 2-3 minutes on first load.
        </div>
        <div style={{fontSize:14,color:'var(--text-secondary)',fontStyle:'italic',
          animation:'pulse 1.4s ease-in-out infinite'}}>
          {steps[step]}
        </div>
      </div>
      <div style={{display:'flex',gap:6,marginTop:8}}>
        {steps.map((_,i)=>(
          <div key={i} style={{
            width:6,height:6,borderRadius:'50%',transition:'background .4s',
            background:i===step?'var(--accent)':'var(--text-muted)',
          }}/>
        ))}
      </div>
    </div>
  )
}

// ── Chat bubble with streaming cursor ─────────────────────────
function Bubble({ msg, charName, charInit, personaName, personaInit }) {
  const isUser = msg.role === 'user'
  return (
    <div style={{display:'flex',flexDirection:isUser?'row-reverse':'row',alignItems:'flex-end',gap:10,margin:'12px 0'}}>
      <div style={{
        width:34,height:34,borderRadius:'50%',flexShrink:0,
        background:isUser?'var(--accent-dim)':'rgba(255,255,255,0.06)',
        border:`1px solid ${isUser?'var(--accent)':'rgba(255,255,255,0.1)'}`,
        display:'flex',alignItems:'center',justifyContent:'center',
        fontFamily:'var(--font-display)',fontSize:13,fontStyle:'italic',
        color:isUser?'var(--accent)':'var(--text-secondary)',
      }}>{isUser?personaInit:charInit}</div>
      <div style={{maxWidth:'72%'}}>
        <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:4,
          textAlign:isUser?'right':'left',letterSpacing:'.04em'}}>
          {isUser?personaName:charName}
        </div>
        <div style={{
          padding:'11px 15px',
          borderRadius:isUser?'18px 18px 4px 18px':'18px 18px 18px 4px',
          background:isUser?'var(--accent-dim)':'var(--bg-card)',
          border:`1px solid ${isUser?'rgba(201,169,110,0.3)':'var(--border)'}`,
          fontSize:14,lineHeight:1.65,color:'var(--text-primary)',
          fontFamily:isUser?'var(--font-body)':'var(--font-display)',
          whiteSpace:'pre-wrap',
        }}>
          {msg.content}
          {msg.streaming && (
            <span style={{display:'inline-block',width:2,height:'1em',background:'var(--accent)',animation:'pulse 1s infinite',verticalAlign:'text-bottom',marginLeft:2}}/>
          )}
        </div>
        {msg.role==='assistant'&&!msg.sovereign&&!msg.streaming&&(
          <div style={{fontSize:10,color:'var(--danger)',marginTop:4,opacity:.6}}>⚠ sovereignty flag</div>
        )}
      </div>
    </div>
  )
}

// ── Play view with SSE streaming ──────────────────────────────
function PlayView({ scenario, persona, onBack, engineModel, contentFilter, preview }) {
  const [sessionId,setSessionId]       = useState(null)
  const [engineReady,setEngineReady]   = useState(false)
  const [messages,setMessages]         = useState([])
  const [input,setInput]               = useState('')
  const [loading,setLoading]           = useState(false)
  const [initError,setInitError]       = useState(null)
  const bodyRef  = useRef()
  const inputRef = useRef()

  const charName   = scenario?.char_name || 'Character'
  const charInit   = init(charName)
  const personaName = persona?.name || 'You'
  const personaInit = personaName[0]?.toLowerCase() || 'p'

  useEffect(() => {
    if (!scenario || !persona) return
    let sid = null

    // Load previous history only for non-preview sessions
    if (!preview) {
      api.getLastSession(scenario.id)
        .then(last => {
          if (last?.history?.length > 0) {
            setMessages(last.history.map(m => ({
              role: m.role, content: m.content, sovereign: true, violations: [],
            })))
          }
        })
        .catch(() => {})
    }

    api.createSession(scenario.id, persona.id, contentFilter || 'off', preview)
      .then(s => {
        sid = s.session_id
        setSessionId(s.session_id)
        // Poll for engine ready
        return api.waitForEngine(s.session_id)
      })
      .then(() => setEngineReady(true))
      .catch(err => {
        console.error(err)
        setInitError(err.message)
      })

    return () => { if (sid) api.endSession(sid).catch(() => {}) }
  }, [scenario?.id, persona?.id])

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [messages, loading, engineReady])

  async function send() {
    if (!input.trim() || !sessionId || loading || !engineReady) return
    const text = input.trim()
    setInput('')
    // Add user message immediately
    setMessages(m => [...m, { role:'user', content:text }])
    setLoading(true)
    // Add a placeholder assistant message for streaming into
    const assistantIdx = messages.length + 1
    setMessages(m => [...m, { role:'assistant', content:'', sovereign:true, violations:[], streaming:true }])

    try {
      const response = await api.playTurnStream(sessionId, text, engineModel)
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullContent = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'token') {
                fullContent += data.content
                // Update the streaming message
                setMessages(prev => {
                  const updated = [...prev]
                  updated[assistantIdx] = { ...updated[assistantIdx], content: fullContent }
                  return updated
                })
              } else if (data.type === 'done') {
                // Finalize the message
                setMessages(prev => {
                  const updated = [...prev]
                  updated[assistantIdx] = {
                    role: 'assistant',
                    content: data.response,
                    sovereign: data.sovereign,
                    violations: data.violations,
                    streaming: false,
                  }
                  return updated
                })
              } else if (data.type === 'error') {
                throw new Error(data.message)
              }
            } catch (e) {
              if (e.message && !e.message.includes('JSON')) throw e
            }
          }
        }
      }
    } catch (err) {
      setMessages(m => [...m, { role:'error', content:err.message }])
      // Remove the streaming placeholder if error
      setMessages(prev => prev.filter((_, i) => i !== assistantIdx))
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  async function handleBack() {
    if (sessionId) await api.endSession(sessionId).catch(() => {})
    onBack()
  }

  return (
    <div className="play-view active" style={{flex:1}}>
      {/* Preview banner */}
      {preview && (
        <div style={{background:'rgba(201,169,110,0.1)',borderBottom:'1px solid rgba(201,169,110,0.3)',
          padding:'6px 16px',fontSize:11,color:'var(--accent)',letterSpacing:'.06em',
          textAlign:'center',textTransform:'uppercase',fontWeight:600}}>
          Preview mode — session not saved to history
        </div>
      )}
      {/* Header */}
      <div className="play-header">
        <button className="play-back" onClick={handleBack}>← Back</button>
        <div style={{display:'flex',alignItems:'center',gap:10}}>
          <div style={{
            width:32,height:32,borderRadius:'50%',
            background:'rgba(255,255,255,0.06)',border:'1px solid rgba(255,255,255,0.1)',
            display:'flex',alignItems:'center',justifyContent:'center',
            fontFamily:'var(--font-display)',fontSize:13,fontStyle:'italic',color:'var(--text-secondary)',
          }}>{charInit}</div>
          <div className="play-title">{scenario?.title || charName}</div>
        </div>
        {persona && (
          <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:8}}>
            <div className="card-persona-dot">{personaInit}</div>
            <span style={{fontSize:13,color:'var(--text-muted)'}}>{personaName}</span>
          </div>
        )}
      </div>

      {/* Body */}
      {initError ? (
        <div style={{flex:1,display:'flex',alignItems:'center',justifyContent:'center',padding:48}}>
          <div style={{textAlign:'center',color:'var(--danger)',fontSize:14}}>
            Engine initialization failed: {initError}
            <div style={{marginTop:12,color:'var(--text-muted)',fontSize:12}}>
              Check your GROQ_API_KEY and try again.
            </div>
          </div>
        </div>
      ) : !engineReady && messages.length === 0 ? (
        <div style={{flex:1}}>
          <InitializingScreen charName={charName}/>
        </div>
      ) : (
        <>
          <div ref={bodyRef} className="chat-body" style={{flex:1,overflowY:'auto',padding:'24px 32px',maxWidth:780,margin:'0 auto',width:'100%'}}>
            {/* Opening scene card */}
            <div style={{
              background:'var(--bg-surface)',border:'1px solid var(--border)',
              borderRadius:14,padding:'20px 24px',marginBottom:24,
              fontFamily:'var(--font-display)',fontSize:16,lineHeight:1.75,
              color:'var(--text-secondary)',whiteSpace:'pre-line',
            }}>
              <div style={{fontSize:11,color:'var(--text-muted)',letterSpacing:'.08em',
                textTransform:'uppercase',marginBottom:12}}>Opening scene</div>
              {scenario?.greeting}
            </div>

            {/* Bubbles */}
            {messages.map((m, i) =>
              m.role === 'error'
                ? <div key={i} style={{color:'var(--danger)',fontSize:13,margin:'8px 0'}}>{m.content}</div>
                : <Bubble key={i} msg={m}
                    charName={charName} charInit={charInit}
                    personaName={personaName} personaInit={personaInit}/>
            )}

            {/* Typing indicator — only show when not streaming */}
            {loading && !messages.some(m => m.streaming) && (
              <div style={{display:'flex',alignItems:'flex-end',gap:10,margin:'12px 0'}}>
                <div style={{
                  width:34,height:34,borderRadius:'50%',
                  background:'rgba(255,255,255,0.06)',border:'1px solid rgba(255,255,255,0.1)',
                  display:'flex',alignItems:'center',justifyContent:'center',
                  fontFamily:'var(--font-display)',fontSize:13,fontStyle:'italic',color:'var(--text-secondary)',
                }}>{charInit}</div>
                <div style={{
                  padding:'11px 18px',borderRadius:'18px 18px 18px 4px',
                  background:'var(--bg-card)',border:'1px solid var(--border)',
                  color:'var(--text-muted)',fontSize:20,letterSpacing:4,
                  animation:'pulse 1.4s ease-in-out infinite',
                }}>···</div>
              </div>
            )}

            {/* Engine still initializing but history already loaded */}
            {!engineReady && messages.length > 0 && (
              <div style={{textAlign:'center',padding:'16px 0',fontSize:12,
                color:'var(--text-muted)',fontStyle:'italic',animation:'pulse 1.4s ease-in-out infinite'}}>
                Engine still initializing — you can read the history while you wait…
              </div>
            )}
          </div>

          {/* Input */}
          <div className="play-input-wrapper" style={{padding:'16px 32px',borderTop:'1px solid var(--border)',
            maxWidth:780,margin:'0 auto',width:'100%',flexShrink:0}}>
            <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:8,letterSpacing:'.04em'}}>
              **action** · "speech" · plain · [To Herself] thought
            </div>
            <div style={{display:'flex',gap:10,alignItems:'flex-end'}}>
              <textarea ref={inputRef} className="play-input" rows={1}
                value={input} onChange={e=>setInput(e.target.value)}
                onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}}
                placeholder={engineReady?'What do you do?':'Waiting for engine…'}
                disabled={!engineReady}
              />
              <button className="play-send" onClick={send} disabled={loading||!sessionId||!engineReady}>
                Send
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ── History tab (with pagination) ─────────────────────────────
function HistoryTab({ scenarios, onReopen }) {
  const [logs,setLogs]           = useState([])
  const [loading,setLoading]     = useState(true)
  const [cursor,setCursor]       = useState(null)
  const [hasMore,setHasMore]     = useState(false)
  const [loadingMore,setLoadingMore] = useState(false)

  async function loadHistory(c) {
    try {
      const res = await api.sessionHistory(c)
      if (c) {
        setLogs(prev => [...prev, ...res.items])
      } else {
        setLogs(res.items)
      }
      setCursor(res.next_cursor)
      setHasMore(res.has_more)
    } catch(e) { console.error(e) }
    finally { setLoading(false); setLoadingMore(false) }
  }

  useEffect(() => { loadHistory(null) }, [])

  function handleLoadMore() {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)
    loadHistory(cursor)
  }

  if (loading) return <div style={{color:'var(--text-muted)',fontSize:13}}>Loading…</div>
  if (!logs.length) return <div style={{color:'var(--text-muted)',fontSize:13}}>No completed sessions yet.</div>
  const byScenario = {}
  logs.forEach(l => { if (!byScenario[l.scenario_id]) byScenario[l.scenario_id] = l })
  return (
    <div>
      {Object.values(byScenario).map(log => {
        const sc = scenarios.find(s => s.id === log.scenario_id)
        return (
          <div key={log.session_id} onClick={() => sc && onReopen(sc)}
            style={{padding:'12px 10px',borderRadius:10,cursor:'pointer',marginBottom:8,
              background:'var(--bg-card)',border:'1px solid var(--border)',transition:'border-color .2s'}}
            onMouseEnter={e=>e.currentTarget.style.borderColor='var(--border-hover)'}
            onMouseLeave={e=>e.currentTarget.style.borderColor='var(--border)'}
          >
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
              <div style={{
                width:28,height:28,borderRadius:'50%',flexShrink:0,
                background:'rgba(255,255,255,0.06)',border:'1px solid rgba(255,255,255,0.1)',
                display:'flex',alignItems:'center',justifyContent:'center',
                fontFamily:'var(--font-display)',fontSize:12,fontStyle:'italic',color:'var(--text-secondary)',
              }}>{init(sc?.char_name||'??')}</div>
              <div style={{fontSize:13,fontWeight:500,color:'var(--text-primary)'}}>
                {sc?.title||sc?.char_name||'Unknown scenario'}
              </div>
            </div>
            <div style={{fontSize:11,color:'var(--text-muted)',paddingLeft:36}}>
              {log.turns_count} turns · {new Date(log.started_at).toLocaleDateString()}
            </div>
          </div>
        )
      })}
      {hasMore && (
        <button className="load-more-btn" onClick={handleLoadMore} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  )
}

// ── Browse tab (public scenarios) ─────────────────────────────
function BrowseTab({ scenarios, onPlay, savedIds, onSave, searchQuery }) {
  const [items,setItems]           = useState([])
  const [cursor,setCursor]         = useState(null)
  const [hasMore,setHasMore]       = useState(false)
  const [loading,setLoading]       = useState(true)
  const [loadingMore,setLoadingMore] = useState(false)
  const [tagFilter,setTagFilter]   = useState('all')

  async function loadMore(c) {
    try {
      const res = await api.listPublicScenarios(c)
      if (c) {
        setItems(prev => [...prev, ...res.items])
      } else {
        setItems(res.items)
      }
      setCursor(res.next_cursor)
      setHasMore(res.has_more)
    } catch(e) { console.error(e) }
    finally { setLoading(false); setLoadingMore(false) }
  }

  useEffect(() => { loadMore(null) }, [])

  function handleLoadMore() {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)
    loadMore(cursor)
  }

  // Filter by tag and search
  const filtered = tagFilter === 'all' ? items : items.filter(s => (s.tags||[]).includes(tagFilter))
  const searchFiltered = searchQuery
    ? filtered.filter(s =>
        (s.title || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (s.char_name || '').toLowerCase().includes(searchQuery.toLowerCase())
      )
    : filtered

  // Determine which scenario IDs belong to the user (from the main scenarios list)
  const ownIds = new Set(scenarios.map(s => s.id))

  if (loading) return <div style={{color:'var(--text-muted)',fontSize:13}}>Loading…</div>
  return (
    <div>
      <div style={{marginBottom:16}}>
        {['all','drama','power','thriller','grief','betrayal','romance'].map(f=>(
          <button key={f} className={`filter-btn ${tagFilter===f?'active':''}`} onClick={()=>setTagFilter(f)} style={{marginRight:4,marginBottom:6}}>{f}</button>
        ))}
      </div>
      {!searchFiltered.length && <div style={{color:'var(--text-muted)',fontSize:13}}>No public scenarios found.</div>}
      <div className="card-grid">
        {searchFiltered.map(s => (
          <ScenarioCard
            key={s.id}
            scenario={s}
            onPlay={onPlay}
            saved={savedIds.has(s.id)}
            onSave={onSave}
            isOwner={ownIds.has(s.id)}
            onEdit={()=>{}}
            onDelete={()=>{}}
          />
        ))}
      </div>
      {hasMore && filtered.length > 0 && (
        <button className="load-more-btn" onClick={handleLoadMore} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more scenarios'}
        </button>
      )}
    </div>
  )
}

// ── Main app ──────────────────────────────────────────────────
function App() {
  const {user,loading:authLoading,logout} = useAuth()
  const qc = useQueryClient()
  const [activeTab,     setActiveTab]     = useState('persona')
  const [scenarioCursor, setScenarioCursor] = useState(null)
  const [scenarioHasMore, setScenarioHasMore] = useState(false)
  const [extraScenarios, setExtraScenarios] = useState([])  // paginated pages 2+
  const [savedIds,      setSavedIds]      = useState(new Set())
  const [activePersona, setActivePersona] = useState(null)
  const [playScenario,  setPlayScenario]  = useState(null)
  const [playPreview,   setPlayPreview]   = useState(false)
  const [filter,        setFilter]        = useState('all')
  const [generating,    setGenerating]    = useState(false)
  const [engineModel,   setEngineModel]   = useState(() => localStorage.getItem('scenarai_model')||'llama-3.1-8b-instant')
  const [contentFilter, setContentFilter] = useState(() => localStorage.getItem('scenarai_filter')||'off')
  const [searchQuery,   setSearchQuery]   = useState('')

  // Persona form state
  const [pName,setPName]=useState(''); const [pPronouns,setPPronouns]=useState('she/her'); const [pBrief,setPBrief]=useState('')
  const [editingPersona, setEditingPersona] = useState(null)

  // Scenario form state
  const [sCharName,setSCharName]=useState(''); const [sCharPronouns,setSCharPronouns]=useState('he/him')
  const [sTitle,setSTitle]=useState(''); const [sPersonality,setSPersonality]=useState('')
  const [sOpening,setSOpening]=useState('')
  const [editingScenario, setEditingScenario] = useState(null)

  // React Query data fetching
  const { data: scenarioPage } = useQuery({
    queryKey: ['scenarios'],
    queryFn: () => api.listScenarios(),
    enabled: !!user,
    refetchInterval: (data) => {
      const items = data?.items || []
      const hasPending = items.some(s => s.is_published && s.prefab_ready === false)
      return hasPending ? 15_000 : false
    },
  })
  const { data: personaPage } = useQuery({
    queryKey: ['personas'],
    queryFn: () => api.listPersonas(),
    enabled: !!user,
  })
  const { data: modelOptions = [] } = useQuery({
    queryKey: ['models'],
    queryFn: () => api.listModels(),
    enabled: !!user,
    staleTime: Infinity,
  })

  const scenarios = [
    ...(scenarioPage?.items || (Array.isArray(scenarioPage) ? scenarioPage : [])),
    ...extraScenarios,
  ]
  const personas  = personaPage?.items || (Array.isArray(personaPage) ? personaPage : []) || []

  // Set active persona once on first load
  useEffect(() => {
    if (personas.length > 0 && !activePersona) setActivePersona(personas[0])
  }, [personas])

  // Sync cursor/hasMore from query result
  useEffect(() => {
    if (!scenarioPage) return
    setScenarioCursor(scenarioPage.next_cursor || null)
    setScenarioHasMore(scenarioPage.has_more || false)
  }, [scenarioPage])

  if (authLoading) return <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',color:'var(--text-muted)'}}>Loading…</div>
  if (!user) return <AuthGate/>

  if (playScenario) return (
    <div style={{display:'flex',height:'100vh',overflow:'hidden'}}>
      <PlayView scenario={playScenario} persona={activePersona} engineModel={engineModel}
        contentFilter={contentFilter} preview={playPreview}
        onBack={()=>{setPlayScenario(null);setPlayPreview(false)}}/>
    </div>
  )

  function changeModel(val) { setEngineModel(val); localStorage.setItem('scenarai_model',val) }

  async function savePersona() {
    if (!pName.trim()) return
    try {
      if (editingPersona) {
        const updated = await api.updatePersona(editingPersona, {name:pName, pronouns:pPronouns, brief:pBrief})
        if (activePersona?.id === editingPersona) setActivePersona(updated)
        setEditingPersona(null)
      } else {
        const p = await api.createPersona({name:pName,pronouns:pPronouns,brief:pBrief})
        setActivePersona(p)
      }
      setPName(''); setPBrief(''); setPPronouns('she/her')
      qc.invalidateQueries({ queryKey: ['personas'] })
    } catch(err) { alert(err.message) }
  }

  function startEditPersona(p) {
    setEditingPersona(p.id)
    setPName(p.name)
    setPPronouns(p.pronouns)
    setPBrief(p.brief||'')
    setActiveTab('persona')
  }

  async function deletePersona(id) {
    if (!confirm('Delete this persona?')) return
    try {
      await api.deletePersona(id)
      if (activePersona?.id === id) setActivePersona(personas.find(p => p.id !== id) || null)
      qc.invalidateQueries({ queryKey: ['personas'] })
    } catch(err) { alert(err.message) }
  }

  async function generateScenario() {
    if (!sCharName||!sTitle||!sPersonality||!sOpening) { alert('Fill in all fields'); return }
    setGenerating(true)
    try {
      await api.createScenario({
        char_name:sCharName, char_pronouns:sCharPronouns,
        char_title:sTitle, char_personality:sPersonality,
        greeting:sOpening,
      })
      setSCharName(''); setSTitle(''); setSPersonality(''); setSOpening('')
      setActiveTab('persona')
      qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch(err) { alert(err.message) }
    finally { setGenerating(false) }
  }

  async function publishScenario(id) {
    try {
      await api.publishScenario(id)
      qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch(err) { alert(err.message) }
  }

  function startEditScenario(s) {
    setEditingScenario(s.id)
    setSCharName(s.char_name)
    setSCharPronouns(s.char_pronouns||'he/him')
    setSTitle(s.char_title)
    setSPersonality(s.char_personality)
    setSOpening(s.greeting)
    setActiveTab('scenario')
  }

  async function saveScenarioEdit() {
    if (!sCharName||!sTitle||!sPersonality||!sOpening) { alert('Fill in all fields'); return }
    setGenerating(true)
    try {
      await api.updateScenario(editingScenario, {
        char_name: sCharName, char_pronouns: sCharPronouns,
        char_title: sTitle, char_personality: sPersonality,
        greeting: sOpening,
      })
      setEditingScenario(null)
      setSCharName(''); setSTitle(''); setSPersonality(''); setSOpening('')
      setActiveTab('persona')
      qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch(err) { alert(err.message) }
    finally { setGenerating(false) }
  }

  async function deleteScenario(id) {
    if (!confirm('Delete this scenario?')) return
    try {
      await api.deleteScenario(id)
      setExtraScenarios(prev => prev.filter(s => s.id !== id))
      qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch(err) { alert(err.message) }
  }

  async function toggleSave(id, isSaved) {
    try {
      if (isSaved) { await api.unsaveScenario(id); setSavedIds(prev=>{const s=new Set(prev);s.delete(id);return s}) }
      else { await api.saveScenario(id); setSavedIds(prev=>new Set([...prev,id])) }
    } catch(err) { console.error(err) }
  }

  function openPlay(sc) {
    if (!activePersona) { alert('Create a persona first'); return }
    if (sc.is_published === false) {
      // Draft — open in preview mode (session not saved to history)
      setPlayPreview(true)
      setPlayScenario(sc)
      return
    }
    if (sc.prefab_ready === false) return
    setPlayPreview(false)
    setPlayScenario(sc)
  }

  // Search filter
  const filtered = filter==='all' ? scenarios : scenarios.filter(s=>(s.tags||[]).includes(filter))
  const searchFiltered = searchQuery
    ? filtered.filter(s =>
        (s.title||'').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (s.char_name||'').toLowerCase().includes(searchQuery.toLowerCase())
      )
    : filtered

  const tabs = ['persona','scenario','browse','history','settings']

  return (
    <div style={{display:'flex',height:'100vh',overflow:'hidden'}}>
      <aside className="sidebar">
        <div className="logo" onClick={()=>setPlayScenario(null)}>
          <div className="logo-word">Scen<em style={{fontStyle:'italic',color:'var(--accent)'}}>ar</em>ai</div>
          <div className="logo-sub">narrative engine</div>
        </div>
        <div className="tab-nav">
          {tabs.map(t=>(
            <button key={t} className={`tab-btn ${activeTab===t?'active':''}`} onClick={()=>setActiveTab(t)}>
              {t==='browse'?'✦ Browse':t.charAt(0).toUpperCase()+t.slice(1)}
            </button>
          ))}
        </div>

        {/* Persona */}
        <div className={`tab-panel ${activeTab==='persona'?'active':''}`}>
          <div className="section-title">Your character</div>
          {personas.length>0&&(
            <div style={{marginBottom:16}}>
              {personas.map(p=>(
                <div key={p.id} style={{
                  padding:'8px 10px',borderRadius:8,cursor:'pointer',marginBottom:4,
                  background:activePersona?.id===p.id?'var(--accent-dim)':'transparent',
                  border:`1px solid ${activePersona?.id===p.id?'var(--accent)':'transparent'}`,
                  display:'flex',alignItems:'center',gap:8,
                }}>
                  <div onClick={()=>setActivePersona(p)} style={{display:'flex',alignItems:'center',gap:8,flex:1}}>
                    <div className="card-persona-dot">{p.name[0].toLowerCase()}</div>
                    <div><div style={{fontSize:13,fontWeight:500}}>{p.name}</div><div style={{fontSize:11,color:'var(--text-muted)'}}>{p.pronouns}</div></div>
                  </div>
                  <button onClick={e=>{e.stopPropagation();startEditPersona(p)}}
                    style={{background:'none',border:'none',cursor:'pointer',fontSize:13,color:'var(--text-muted)',padding:4}}
                    title="Edit persona">✎</button>
                  <button onClick={e=>{e.stopPropagation();deletePersona(p.id)}}
                    style={{background:'none',border:'none',cursor:'pointer',fontSize:13,color:'var(--text-muted)',padding:4}}
                    title="Delete persona">×</button>
                </div>
              ))}
              <div style={{borderTop:'1px solid var(--border)',marginTop:12,paddingTop:12,fontSize:11,color:'var(--text-muted)',textTransform:'uppercase',letterSpacing:'.08em'}}>Add new</div>
            </div>
          )}
          <div className="field"><label>Name</label><input type="text" value={pName} onChange={e=>setPName(e.target.value)} placeholder="e.g. mario"/></div>
          <div className="field"><label>Pronouns</label>
            <select value={pPronouns} onChange={e=>setPPronouns(e.target.value)}>
              <option value="she/her">she / her</option><option value="he/him">he / him</option><option value="they/them">they / them</option>
            </select>
          </div>
          <div className="field"><label>Brief (optional)</label><textarea value={pBrief} onChange={e=>setPBrief(e.target.value)} placeholder="A few words…"/></div>
          {editingPersona && (
            <button className="btn btn-ghost" onClick={() => {setEditingPersona(null);setPName('');setPPronouns('she/her');setPBrief('')}}>Cancel edit</button>
          )}
          <button className="btn btn-primary" onClick={savePersona}>{editingPersona?'Update persona':'Save persona'}</button>
          <button className="btn btn-ghost" style={{marginTop:24}} onClick={logout}>Sign out</button>
        </div>

        {/* Scenario */}
        <div className={`tab-panel ${activeTab==='scenario'?'active':''}`}>
          <div className="section-title">{editingScenario ? 'Edit scenario' : 'New scenario'}</div>
          <div className="field"><label>Character name *</label><input type="text" value={sCharName} onChange={e=>setSCharName(e.target.value)} placeholder="e.g. Malthe Richter"/></div>
          <div className="field"><label>Pronouns</label>
            <select value={sCharPronouns} onChange={e=>setSCharPronouns(e.target.value)}>
              <option value="he/him">he / him</option><option value="she/her">she / her</option><option value="they/them">they / them</option>
            </select>
          </div>
          <div className="field"><label>Role / title *</label><input type="text" value={sTitle} onChange={e=>setSTitle(e.target.value)} placeholder="e.g. A narcissistic husband who…"/></div>
          <div className="field"><label>Personality *</label><textarea value={sPersonality} onChange={e=>setSPersonality(e.target.value)} placeholder="Psychology, drives, fears…" style={{minHeight:90}}/></div>
          <div className="field"><label>Opening scene *</label><textarea value={sOpening} onChange={e=>setSOpening(e.target.value)} placeholder="Set the scene. 'you' = your persona." style={{minHeight:90}}/></div>
          {editingScenario
            ? <>
                <button className="btn btn-primary" onClick={saveScenarioEdit} disabled={generating}>{generating?'Updating…':'Update scenario'}</button>
                <button className="btn btn-ghost" onClick={() => {setEditingScenario(null);setSCharName('');setSTitle('');setSPersonality('');setSOpening('')}}>Cancel edit</button>
              </>
            : <>
                <button className="btn btn-primary" onClick={generateScenario} disabled={generating}>{generating?'Generating…':'Generate scenario'}</button>
                <button className="btn btn-ghost" onClick={()=>{setSCharName('');setSTitle('');setSPersonality('');setSOpening('')}}>Clear</button>
              </>
          }
        </div>

        {/* Browse */}
        <div className={`tab-panel ${activeTab==='browse'?'active':''}`}>
          <div className="section-title">Discover scenarios</div>
          <BrowseTab
            scenarios={scenarios}
            onPlay={openPlay}
            savedIds={savedIds}
            onSave={toggleSave}
            searchQuery={searchQuery}
          />
        </div>

        {/* History */}
        <div className={`tab-panel ${activeTab==='history'?'active':''}`}>
          <div className="section-title">Recent sessions</div>
          <HistoryTab scenarios={scenarios} onReopen={openPlay}/>
        </div>

        {/* Settings */}
        <div className={`tab-panel ${activeTab==='settings'?'active':''}`}>
          <div className="section-title">Engine</div>
          <div className="field">
            <label>Play model</label>
            <select value={engineModel} onChange={e=>changeModel(e.target.value)}>
              {modelOptions.length>0
                ?modelOptions.map(m=><option key={m.id} value={m.id}>{m.label}</option>)
                :<>
                  <option value="llama-3.1-8b-instant">LLaMA 3.1 8B — fastest</option>
                  <option value="llama-3.3-70b-versatile">LLaMA 3.3 70B — best quality</option>
                  <option value="llama-3.1-70b-versatile">LLaMA 3.1 70B — balanced</option>
                  <option value="gemma2-9b-it">Gemma 2 9B — Google</option>
                  <option value="mixtral-8x7b-32768">Mixtral 8x7B — large context</option>
                </>
              }
            </select>
          </div>
          <div style={{fontSize:12,color:'var(--text-muted)',lineHeight:1.6,marginTop:8}}>
            Saved automatically. Takes effect on the next session.
          </div>
          <div style={{marginTop:20,padding:'12px 14px',background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:10}}>
            <div style={{fontSize:11,color:'var(--text-secondary)',marginBottom:4}}>Active model</div>
            <div style={{fontSize:13,color:'var(--accent)',fontWeight:500}}>{engineModel}</div>
          </div>

          <div className="section-title" style={{marginTop:24}}>Content filter</div>
          <div className="field">
            <label>Filter level</label>
            <select value={contentFilter} onChange={e => {setContentFilter(e.target.value); localStorage.setItem('scenarai_filter', e.target.value)}}>
              <option value="off">Off — no restrictions</option>
              <option value="on">Safe — fade to black</option>
              <option value="force">Strict — always safe (locked)</option>
            </select>
          </div>
          <div style={{fontSize:12,color:'var(--text-muted)',lineHeight:1.6,marginTop:8}}>
            Controls content filtering in roleplay sessions. Takes effect on the next session.
          </div>
          <div style={{marginTop:20,padding:'12px 14px',background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:10}}>
            <div style={{fontSize:11,color:'var(--text-secondary)',marginBottom:4}}>Active filter</div>
            <div style={{fontSize:13,color:'var(--accent)',fontWeight:500}}>
              {contentFilter === 'off' ? 'Off' : contentFilter === 'on' ? 'Safe' : 'Strict'}
            </div>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="topbar-title">
            {activeTab === 'browse'
              ? <><strong>Discover</strong> — public scenarios</>
              : <><strong>All scenarios</strong> — pick one to play</>
            }
          </div>
          <input
            className="search-input"
            type="text"
            placeholder="Search scenarios…"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
          <div className="topbar-right topbar-center">
            {['all','drama','power','thriller','grief','betrayal'].map(f=>(
              <button key={f} className={`filter-btn ${filter===f?'active':''}`} onClick={()=>setFilter(f)}>{f}</button>
            ))}
          </div>
        </div>
        <div className="grid-section">
          <div className="grid-label">
            {scenarios.length===0?'No scenarios yet — create one in the sidebar':`${searchFiltered.length} scenario${searchFiltered.length!==1?'s':''}`}
          </div>
          <div className="card-grid">
            {searchFiltered.map(s=>(
              <ScenarioCard
                key={s.id}
                scenario={s}
                saved={savedIds.has(s.id)}
                onSave={toggleSave}
                onPlay={openPlay}
                isOwner={true}
                onEdit={startEditScenario}
                onDelete={deleteScenario}
                onPublish={publishScenario}
              />
            ))}
            <div className="card-add" onClick={()=>{setEditingScenario(null);setActiveTab('scenario')}}>
              <div className="card-add-icon">+</div>
              <div className="card-add-label">Create scenario</div>
            </div>
          </div>
          {scenarioHasMore && (
            <button
              className="load-more-btn"
              onClick={async () => {
                try {
                  const res = await api.listScenarios(scenarioCursor)
                  setExtraScenarios(prev => [...prev, ...(res.items||[])])
                  setScenarioCursor(res.next_cursor)
                  setScenarioHasMore(res.has_more)
                } catch(e) { console.error(e) }
              }}
            >
              Load more
            </button>
          )}
        </div>
      </main>
    </div>
  )
}

// Wrap with ErrorBoundary
function AppWithErrorBoundary() {
  return (
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  )
}

export default AppWithErrorBoundary
