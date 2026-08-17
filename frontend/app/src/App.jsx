import { useState, useEffect, useRef, Component } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { useAuth } from './AuthContext'

// Errors stay loud in dev, silent in prod builds -- import.meta.env.DEV
// is Vite's own flag (the process.env.NODE_ENV equivalent CRA/webpack
// code uses doesn't exist in a Vite bundle).
function logError(...args) { if (import.meta.env.DEV) console.error(...args) }

// ── Error Boundary ────────────────────────────────────────────
class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null } }
  static getDerivedStateFromError(error) { return { hasError: true, error } }
  componentDidCatch(error, info) { logError('ErrorBoundary:', error, info) }
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
function ScenarioCard({ scenario, onPlay, saved, onSave, onEdit, onDelete, onPublish, onUnpublish, onReport, isOwner }) {
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
          {!isDraft && !isOwner && onReport && (
            <button onClick={e=>{e.stopPropagation();onReport(scenario.id)}}
              style={{background:'none',border:'none',cursor:'pointer',fontSize:13,
                color:'var(--text-muted)',transition:'color .2s',padding:'0 2px'}}
              title="Report this scenario">⚑</button>
          )}
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
              {isDraft && <button className="card-action-btn danger" onClick={e=>{e.stopPropagation();setMenuOpen(false);onDelete(scenario.id)}}>Delete</button>}
              {!isDraft && onUnpublish && <button className="card-action-btn" onClick={e=>{e.stopPropagation();setMenuOpen(false);onUnpublish(scenario.id)}}
                style={{color:'var(--text-muted)'}}>Unpublish</button>}
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
// Turn feedback rating — helpful signal for future quality work (complements
// the offline judge harness in judge/ with real user feedback, not just
// scripted golden scenarios). 1-5 stars, upserts via api.rateTurn.
function RatingStars({ sessionId, turn }) {
  const [rating, setRating]   = useState(null)
  const [hover, setHover]     = useState(0)
  const [saving, setSaving]   = useState(false)

  if (!sessionId || !turn) return null

  async function rate(value) {
    if (saving) return
    const prev = rating
    setRating(value) // optimistic
    setSaving(true)
    try {
      await api.rateTurn(sessionId, turn, value)
    } catch (e) {
      logError(e)
      setRating(prev)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{display:'flex',gap:2,marginTop:4,alignItems:'center'}} title="Rate this turn">
      {[1,2,3,4,5].map(n => (
        <span key={n}
          onClick={() => rate(n)}
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(0)}
          style={{
            cursor:'pointer', fontSize:13, lineHeight:1,
            color: n <= (hover || rating || 0) ? 'var(--accent)' : 'var(--text-muted)',
            opacity: n <= (hover || rating || 0) ? 1 : 0.4,
            transition:'color .1s, opacity .1s',
          }}
        >★</span>
      ))}
      {rating && <span style={{fontSize:10,color:'var(--text-muted)',marginLeft:4}}>rated</span>}
    </div>
  )
}

// Per-turn generated video segment (image + narration baked in by the
// worker's bridge pipeline) shown inside an assistant bubble. Reuses the
// single stitched mp4 for both the static "scene image" and the narration
// audio rather than wiring up separate <img>/<audio> elements.
function TurnMediaCard({ media, muted, isLast, onRegenerate, regenerating }) {
  if (!media) return null
  if (media.status === 'pending') {
    return (
      <div style={{fontSize:11,color:'var(--text-muted)',fontStyle:'italic',margin:'2px 0 6px'}}>
        Generating scene…
      </div>
    )
  }
  if (media.status === 'failed') {
    return (
      <div style={{fontSize:11,color:'var(--danger)',margin:'2px 0 6px',display:'flex',alignItems:'center',gap:8}}>
        <span>Scene generation failed{media.error ? `: ${media.error}` : ''}</span>
        {isLast && onRegenerate && (
          <button onClick={onRegenerate} disabled={regenerating}
            style={{background:'none',border:'none',color:'var(--danger)',textDecoration:'underline',
              cursor:regenerating?'default':'pointer',fontSize:11,padding:0,opacity:regenerating?0.5:1}}>
            {regenerating ? 'retrying…' : 'retry'}
          </button>
        )}
      </div>
    )
  }
  if (media.status !== 'ready' || !media.video_segment_url) return null
  return (
    <div style={{position:'relative',borderRadius:14,overflow:'hidden',marginBottom:6,border:'1px solid var(--border)',background:'#000'}}>
      <video
        src={media.video_segment_url}
        muted={muted}
        autoPlay={isLast}
        controls
        playsInline
        style={{display:'block',width:'100%',maxHeight:360}}
      />
      {isLast && onRegenerate && (
        <button onClick={onRegenerate} disabled={regenerating}
          title="Not happy with this scene? Regenerate it"
          style={{
            position:'absolute',top:8,right:8,width:26,height:26,borderRadius:'50%',
            background:'rgba(0,0,0,0.55)',border:'1px solid rgba(255,255,255,0.25)',
            color:'#fff',fontSize:14,lineHeight:1,cursor:regenerating?'default':'pointer',
            display:'flex',alignItems:'center',justifyContent:'center',
            opacity:regenerating?0.5:0.9,
          }}
        >{regenerating ? '…' : '⋯'}</button>
      )}
    </div>
  )
}

function Bubble({ msg, charName, charInit, personaName, personaInit, sessionId, isLast, onRegenerate, regenerating, media, muted, onRegenerateMedia, regeneratingMedia }) {
  const isUser = msg.role === 'user'
  const hasImage = !isUser && media?.status === 'ready' && media.video_segment_url
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
          {msg.isContinuation && <span style={{fontStyle:'italic',opacity:.7}}> · continued</span>}
        </div>
        {!isUser && (
          <TurnMediaCard media={media} muted={muted} isLast={isLast}
            onRegenerate={onRegenerateMedia} regenerating={regeneratingMedia}/>
        )}
        <div style={{
          padding:'11px 15px',
          borderRadius:isUser?'18px 18px 4px 18px':'18px 18px 18px 4px',
          background:isUser?'var(--accent-dim)':(hasImage?'rgba(26,26,30,0.6)':'var(--bg-card)'),
          border:`1px solid ${isUser?'rgba(201,169,110,0.3)':'var(--border)'}`,
          backdropFilter:hasImage?'blur(6px)':undefined,
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
        {msg.role==='assistant'&&!msg.streaming&&(
          <div style={{display:'flex',alignItems:'center',gap:10}}>
            <RatingStars sessionId={sessionId} turn={msg.turn}/>
            {/* Only on the most recent reply — a single generation coming out
                wrong doesn't mean the whole turn was wrong, try again
                (SpicyChat-style "regenerate"). Regenerating an older message
                would desync it from turns that already followed it. */}
            {isLast && onRegenerate && (
              <button onClick={onRegenerate} disabled={regenerating}
                title="Discard this reply and try again"
                style={{
                  background:'none',border:'none',cursor:regenerating?'default':'pointer',
                  color:'var(--text-muted)',fontSize:10,letterSpacing:'.04em',
                  textDecoration:'underline',opacity:regenerating?0.4:0.7,padding:0,
                }}
              >
                {regenerating ? 'regenerating…' : '↻ regenerate'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// Sequences ready per-turn video segments as a playlist -- true server-side
// live-concat into one continuously growing file is a documented follow-up
// (see worker.py's generate_turn_media_job docstring); this plays the same
// per-turn segments back-to-back client-side instead, auto-advancing as
// each one ends.
function MoviePlayer({ turnMedia, messages, muted, charName, sessionId, movieInfo }) {
  const ready = Object.values(turnMedia)
    .filter(m => m.status === 'ready' && m.video_segment_url)
    .sort((a, b) => a.turn - b.turn)
  const [idx, setIdx] = useState(0)
  const videoRef = useRef()
  const clampedIdx = Math.min(idx, Math.max(0, ready.length - 1))

  const [exportFrom, setExportFrom] = useState('')
  const [exportTo, setExportTo]     = useState('')
  const [exporting, setExporting]   = useState(false)
  const [exportResult, setExportResult] = useState(null)
  const [exportError, setExportError]   = useState(null)

  useEffect(() => {
    if (ready.length > 0 && exportFrom === '') {
      setExportFrom(String(ready[0].turn))
      setExportTo(String(ready[ready.length - 1].turn))
    }
  }, [ready.length])

  async function doExport() {
    if (!sessionId || exportFrom === '' || exportTo === '' || exporting) return
    setExporting(true); setExportError(null); setExportResult(null)
    try {
      const res = await api.exportSessionVideo(sessionId, Number(exportFrom), Number(exportTo))
      setExportResult(res)
    } catch (err) {
      setExportError(err.message)
    } finally {
      setExporting(false)
    }
  }

  useEffect(() => {
    videoRef.current?.play().catch(() => {})
  }, [clampedIdx, ready[clampedIdx]?.video_segment_url])

  if (ready.length === 0) {
    return (
      <div style={{flex:1,display:'flex',alignItems:'center',justifyContent:'center',padding:48}}>
        <div style={{textAlign:'center',color:'var(--text-muted)'}}>
          <div style={{fontSize:36,opacity:.5,marginBottom:8}}>🎬</div>
          <div style={{fontSize:13}}>No scenes generated yet — keep playing, scenes appear here as they render.</div>
        </div>
      </div>
    )
  }

  const current = ready[clampedIdx]
  const turnText = messages.find(m => m.turn === current.turn && m.role === 'assistant')?.content

  return (
    <div style={{flex:1,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',padding:'24px 32px',gap:16,overflowY:'auto'}}>
      <video
        key={current.video_segment_url}
        ref={videoRef}
        src={current.video_segment_url}
        muted={muted}
        controls
        playsInline
        autoPlay
        onEnded={() => setIdx(i => Math.min(i + 1, ready.length - 1))}
        style={{maxWidth:640,width:'100%',borderRadius:14,border:'1px solid var(--border)',background:'#000'}}
      />
      {turnText && (
        <div style={{maxWidth:640,fontSize:13,color:'var(--text-secondary)',textAlign:'center',lineHeight:1.6,fontFamily:'var(--font-display)'}}>
          {turnText}
        </div>
      )}
      <div style={{display:'flex',alignItems:'center',gap:12}}>
        <button onClick={() => setIdx(i => Math.max(0, i - 1))} disabled={clampedIdx === 0}
          style={{background:'none',border:'1px solid var(--border)',borderRadius:8,padding:'6px 14px',
            color:'var(--text-secondary)',cursor:clampedIdx===0?'default':'pointer',opacity:clampedIdx===0?.4:1}}>
          ← Prev
        </button>
        <span style={{fontSize:11,color:'var(--text-muted)',letterSpacing:'.04em'}}>
          Scene {clampedIdx + 1} of {ready.length} · Turn {current.turn} · {charName}
        </span>
        <button onClick={() => setIdx(i => Math.min(ready.length - 1, i + 1))} disabled={clampedIdx === ready.length - 1}
          style={{background:'none',border:'1px solid var(--border)',borderRadius:8,padding:'6px 14px',
            color:'var(--text-secondary)',cursor:clampedIdx===ready.length-1?'default':'pointer',opacity:clampedIdx===ready.length-1?.4:1}}>
          Next →
        </button>
      </div>

      {/* Always-current whole-session file, kept up to date server-side as
          each turn's media completes (worker._extend_session_movie) --
          separate from the ad-hoc turn-range export below. */}
      {movieInfo?.available && (
        <a href={movieInfo.url} download
          style={{fontSize:12,color:'var(--accent)',textDecoration:'underline'}}>
          ⬇ Download full movie so far ({ready.length} scene{ready.length===1?'':'s'})
        </a>
      )}

      {/* Export: pick a turn range from the ready scenes and combine them
          into one downloadable mp4 (backend concatenates via ffmpeg). */}
      <div style={{display:'flex',alignItems:'center',gap:8,fontSize:12,color:'var(--text-secondary)'}}>
        <span style={{color:'var(--text-muted)'}}>Export scenes</span>
        <select value={exportFrom} onChange={e=>setExportFrom(e.target.value)}
          style={{background:'var(--bg-card)',color:'var(--text-primary)',border:'1px solid var(--border)',borderRadius:6,padding:'4px 6px'}}>
          {ready.map(m => <option key={m.turn} value={m.turn}>Turn {m.turn}</option>)}
        </select>
        <span style={{color:'var(--text-muted)'}}>to</span>
        <select value={exportTo} onChange={e=>setExportTo(e.target.value)}
          style={{background:'var(--bg-card)',color:'var(--text-primary)',border:'1px solid var(--border)',borderRadius:6,padding:'4px 6px'}}>
          {ready.map(m => <option key={m.turn} value={m.turn}>Turn {m.turn}</option>)}
        </select>
        <button onClick={doExport} disabled={exporting}
          style={{background:'var(--accent-dim)',border:'1px solid rgba(201,169,110,0.3)',borderRadius:6,
            padding:'5px 12px',color:'var(--accent)',cursor:exporting?'default':'pointer',opacity:exporting?.6:1}}>
          {exporting ? 'Combining…' : 'Export'}
        </button>
      </div>
      {exportError && <div style={{fontSize:11,color:'var(--danger)'}}>{exportError}</div>}
      {exportResult && (
        <a href={exportResult.download_url} download
          style={{fontSize:12,color:'var(--accent)',textDecoration:'underline'}}>
          ⬇ Download exported scene ({exportResult.turns.length} turn{exportResult.turns.length===1?'':'s'})
        </a>
      )}
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
  const [regenerating,setRegenerating] = useState(false)
  const [initError,setInitError]       = useState(null)
  // Per-turn generated media (image+narration+video), keyed by turn number —
  // populated by polling GET /sessions/{id}/media, since generation runs as
  // a background arq job and isn't ready by the time the turn's text is.
  const [turnMedia,setTurnMedia]       = useState({})
  const [regeneratingMediaTurn,setRegeneratingMediaTurn] = useState(null)
  // Always-current whole-session movie file (see worker._extend_session_movie) --
  // separate from per-turn turnMedia, polled alongside it.
  const [movieInfo,setMovieInfo]       = useState({ available:false, url:null })
  const [muted,setMuted]               = useState(() => localStorage.getItem('scenarai_muted') === '1')
  const [movieMode,setMovieMode]       = useState(false)
  const bodyRef  = useRef()
  const inputRef = useRef()
  // Synchronous re-entry guard for send() -- `loading` is React state and
  // isn't updated until the next render, so a double-click/double-Enter
  // before that render commits would otherwise pass the `loading` check
  // twice and race on the same assistantIdx. Refs update immediately.
  const sendingRef = useRef(false)
  // Tracks the in-flight stream's AbortController so navigating away
  // mid-response can cancel it instead of leaving an orphaned reader loop
  // running against an unmounted view.
  const streamAbortRef = useRef(null)

  function toggleMuted() {
    setMuted(m => {
      localStorage.setItem('scenarai_muted', m ? '0' : '1')
      return !m
    })
  }

  async function regenerateMedia(turn) {
    if (!sessionId || regeneratingMediaTurn) return
    setRegeneratingMediaTurn(turn)
    setTurnMedia(prev => ({ ...prev, [turn]: { ...prev[turn], status:'pending' } }))
    try {
      await api.regenerateTurnMedia(sessionId, turn)
    } catch (err) {
      logError(err)
      setTurnMedia(prev => ({ ...prev, [turn]: { ...prev[turn], status:'failed', error: err.message } }))
    } finally {
      setRegeneratingMediaTurn(null)
    }
  }

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
            // Best-effort turn numbering for resumed history: getLastSession
            // doesn't return per-message turn numbers, so this counts
            // assistant messages positionally. Accurate for ordinary 1-user-
            // then-1-assistant sessions; a continuation turn in the resumed
            // history could shift it — live messages this session (below)
            // always get the real turn number from the API response.
            let turnCounter = 0
            setMessages(last.history.map(m => ({
              role: m.role, content: m.content, sovereign: true, violations: [],
              turn: m.role === 'assistant' ? ++turnCounter : undefined,
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
        logError(err)
        setInitError(err.message)
      })

    return () => {
      streamAbortRef.current?.abort()
      if (sid) api.endSession(sid).catch(() => {})
    }
  }, [scenario?.id, persona?.id])

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [messages, loading, engineReady])

  // Poll for per-turn media rather than opening a socket for what's a low-
  // frequency, best-effort background job — a turn's text lands immediately,
  // its image/narration/video typically follow within ~10-20s.
  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    async function poll() {
      try {
        const rows = await api.getTurnMedia(sessionId)
        if (cancelled) return
        setTurnMedia(prev => {
          const next = { ...prev }
          for (const row of rows) next[row.turn] = row
          return next
        })
      } catch (err) {
        logError(err)
      }
      try {
        const movie = await api.getSessionMovie(sessionId)
        if (!cancelled) setMovieInfo(movie)
      } catch (err) {
        logError(err)
      }
    }
    poll()
    const id = setInterval(poll, 4000)
    return () => { cancelled = true; clearInterval(id) }
  }, [sessionId])

  async function send() {
    if (!input.trim() || !sessionId || loading || regenerating || !engineReady) return
    if (sendingRef.current) return
    sendingRef.current = true
    const text = input.trim()
    setInput('')
    // Add user message immediately
    setMessages(m => [...m, { role:'user', content:text }])
    setLoading(true)
    // Add a placeholder assistant message for streaming into
    const assistantIdx = messages.length + 1
    setMessages(m => [...m, { role:'assistant', content:'', sovereign:true, violations:[], streaming:true }])

    const controller = new AbortController()
    streamAbortRef.current = controller

    try {
      const response = await api.playTurnStream(sessionId, text, engineModel, controller.signal)
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
            // Parse failures here are a genuinely-incomplete SSE chunk (the
            // rest arrives in a later read()) -- skip just this line, don't
            // treat it as fatal. Narrowly scoped so it can never also catch
            // (and silently swallow) the intentional throw below for a real
            // backend error, whatever that error's message text happens to be.
            let data
            try {
              data = JSON.parse(line.slice(6))
            } catch {
              continue
            }
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
                  turn: data.turn,
                  streaming: false,
                }
                return updated
              })
            } else if (data.type === 'error') {
              throw new Error(data.message)
            }
          }
        }
      }
    } catch (err) {
      // AbortError means the view is being torn down (see the unmount
      // cleanup below) -- the user already navigated away, so there's no
      // bubble left to show an error in and nothing to clean up either.
      if (err.name !== 'AbortError') {
        setMessages(m => [...m, { role:'error', content:err.message }])
        // Remove the streaming placeholder if error
        setMessages(prev => prev.filter((_, i) => i !== assistantIdx))
      }
    } finally {
      sendingRef.current = false
      streamAbortRef.current = null
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  // No player input this turn — let the narrator advance the scene on its
  // own. Not streamed (unlike send()): /continue is a plain request/response
  // endpoint, same shape as the non-streaming /turn.
  async function continueStory() {
    if (!sessionId || loading || regenerating || !engineReady) return
    setLoading(true)
    try {
      const data = await api.continueTurn(sessionId, engineModel)
      setMessages(m => [...m, {
        role: 'assistant', content: data.response,
        sovereign: data.sovereign, violations: data.violations,
        turn: data.turn, isContinuation: true,
      }])
    } catch (err) {
      setMessages(m => [...m, { role:'error', content:err.message }])
    } finally {
      setLoading(false)
    }
  }

  // Discard the last reply and try again for the same input — replaces the
  // last message in place, does not append a new one.
  async function regenerateStory() {
    if (!sessionId || loading || regenerating || !engineReady) return
    setRegenerating(true)
    try {
      const data = await api.regenerateTurn(sessionId, engineModel)
      setMessages(m => {
        const updated = [...m]
        const lastIdx = updated.length - 1
        if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
          updated[lastIdx] = {
            ...updated[lastIdx],
            content: data.response, sovereign: data.sovereign,
            violations: data.violations, turn: data.turn,
          }
        }
        return updated
      })
    } catch (err) {
      setMessages(m => [...m, { role:'error', content:err.message }])
    } finally {
      setRegenerating(false)
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
        <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:14}}>
          <button onClick={toggleMuted} title={muted?'Unmute scene narration':'Mute scene narration'}
            style={{background:'none',border:'1px solid var(--border)',borderRadius:8,width:30,height:30,
              cursor:'pointer',color:'var(--text-secondary)',fontSize:14,display:'flex',
              alignItems:'center',justifyContent:'center'}}>
            {muted ? '🔇' : '🔊'}
          </button>
          <button onClick={()=>setMovieMode(v=>!v)} title="Watch generated scenes as a movie"
            style={{background:movieMode?'var(--accent-dim)':'none',
              border:`1px solid ${movieMode?'var(--accent)':'var(--border)'}`,borderRadius:8,
              padding:'5px 12px',cursor:'pointer',color:movieMode?'var(--accent)':'var(--text-secondary)',
              fontSize:12,letterSpacing:'.04em'}}>
            {movieMode ? '💬 Chat' : '🎬 Movie'}
          </button>
          {persona && (
            <div style={{display:'flex',alignItems:'center',gap:8}}>
              <div className="card-persona-dot">{personaInit}</div>
              <span style={{fontSize:13,color:'var(--text-muted)'}}>{personaName}</span>
            </div>
          )}
        </div>
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
      ) : movieMode ? (
        <MoviePlayer turnMedia={turnMedia} messages={messages} muted={muted} charName={charName} sessionId={sessionId} movieInfo={movieInfo}/>
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
                ? <div key={`err-${i}-${m.content.slice(0,16)}`} style={{color:'var(--danger)',fontSize:13,margin:'8px 0'}}>{m.content}</div>
                : <Bubble key={`${m.role}-${i}-${(m.content||'').slice(0,16)}`} msg={m}
                    charName={charName} charInit={charInit}
                    personaName={personaName} personaInit={personaInit}
                    sessionId={sessionId}
                    isLast={i === messages.length - 1 && m.role === 'assistant'}
                    onRegenerate={regenerateStory}
                    regenerating={regenerating}
                    media={m.turn ? turnMedia[m.turn] : undefined}
                    muted={muted}
                    onRegenerateMedia={m.turn ? () => regenerateMedia(m.turn) : undefined}
                    regeneratingMedia={m.turn === regeneratingMediaTurn}/>
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
            <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:8,letterSpacing:'.04em',display:'flex',justifyContent:'space-between'}}>
              <span>**action** · "speech" · plain · [To Herself] thought</span>
              <button
                onClick={continueStory}
                disabled={loading||regenerating||!sessionId||!engineReady}
                title="Let the narrator continue the scene without your input"
                style={{
                  background:'none',border:'none',cursor:loading||regenerating||!sessionId||!engineReady?'default':'pointer',
                  color:'var(--text-muted)',fontSize:11,letterSpacing:'.04em',textDecoration:'underline',
                  opacity:loading||regenerating||!sessionId||!engineReady?0.4:0.85,padding:0,
                }}
              >
                ↻ Let it continue
              </button>
            </div>
            <div style={{display:'flex',gap:10,alignItems:'flex-end'}}>
              <textarea ref={inputRef} className="play-input" rows={1}
                value={input} onChange={e=>setInput(e.target.value)}
                onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}}
                placeholder={engineReady?'What do you do?':'Waiting for engine…'}
                disabled={!engineReady}
              />
              <button className="play-send" onClick={send} disabled={loading||regenerating||!sessionId||!engineReady}>
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
    } catch(e) { logError(e) }
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
              {log.thumbnail_url ? (
                <img src={log.thumbnail_url} alt="" style={{
                  width:28,height:28,borderRadius:8,flexShrink:0,objectFit:'cover',
                  border:'1px solid rgba(255,255,255,0.1)',
                }}/>
              ) : (
                <div style={{
                  width:28,height:28,borderRadius:'50%',flexShrink:0,
                  background:'rgba(255,255,255,0.06)',border:'1px solid rgba(255,255,255,0.1)',
                  display:'flex',alignItems:'center',justifyContent:'center',
                  fontFamily:'var(--font-display)',fontSize:12,fontStyle:'italic',color:'var(--text-secondary)',
                }}>{init(sc?.char_name||'??')}</div>
              )}
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
function BrowseTab({ scenarios, onPlay, savedIds, onSave, searchQuery, onReport, onUnpublish }) {
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
    } catch(e) { logError(e) }
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
            onUnpublish={onUnpublish}
            onReport={!ownIds.has(s.id) ? onReport : undefined}
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
  const [engineModel,   setEngineModel]   = useState(() => localStorage.getItem('scenarai_model')||'openai/gpt-oss-20b')
  const [contentFilter, setContentFilter] = useState(() => localStorage.getItem('scenarai_filter')||'off')
  const [searchQuery,   setSearchQuery]   = useState('')
  // Dismissible error toast, replacing alert(err.message) across the
  // persona/scenario/account forms below -- a blocking native alert()
  // interrupts every action the same way regardless of severity; this
  // shows the message without stopping the user from doing anything else.
  const [errorToast,    setErrorToast]    = useState(null)
  function showError(err) { setErrorToast(err?.message || String(err)) }
  useEffect(() => {
    if (!errorToast) return
    const t = setTimeout(() => setErrorToast(null), 6000)
    return () => clearTimeout(t)
  }, [errorToast])

  // Persona form state
  const [pName,setPName]=useState(''); const [pPronouns,setPPronouns]=useState('she/her'); const [pBrief,setPBrief]=useState('')
  const [editingPersona, setEditingPersona] = useState(null)

  // Scenario form state
  const [sCharName,setSCharName]=useState(''); const [sCharPronouns,setSCharPronouns]=useState('he/him')
  const [sTitle,setSTitle]=useState(''); const [sPersonality,setSPersonality]=useState('')
  const [sOpening,setSOpening]=useState('')
  const [editingScenario, setEditingScenario] = useState(null)

  // Account settings state
  const [pwCurrent,setPwCurrent]=useState(''); const [pwNew,setPwNew]=useState(''); const [pwConfirm,setPwConfirm]=useState('')
  const [pwLoading,setPwLoading]=useState(false); const [pwMsg,setPwMsg]=useState(null)

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
  // Falls forward to a real model once the list loads if the current
  // engineModel isn't in it -- catches both the hardcoded initial default
  // and, more importantly, a model ID a returning user already has cached
  // in localStorage from before a backend model was retired (Groq
  // decommissioned this app's entire previous model roster in one go;
  // nothing but this check would ever notice a stale cached choice).
  useEffect(() => {
    if (modelOptions.length === 0) return
    if (!modelOptions.some(m => m.id === engineModel)) {
      changeModel(modelOptions[0].id)
    }
  }, [modelOptions])
  // Mock billing (see backend/models.py's UserSubscription docstring) --
  // 404s until MOCK_BILLING_ENABLED is on, so retry:false + the ?? []
  // fallback below mean the tab just renders empty rather than erroring
  // for real users while the flag is off.
  const { data: tiers = [] } = useQuery({
    queryKey: ['billing-tiers'],
    queryFn: () => api.listTiers(),
    enabled: !!user && activeTab === 'billing',
    retry: false,
  })
  const { data: mySubscription } = useQuery({
    queryKey: ['my-subscription'],
    queryFn: () => api.getMySubscription(),
    enabled: !!user && activeTab === 'billing',
    retry: false,
  })
  const [subscribing, setSubscribing] = useState(null)  // tier id currently being mock-subscribed to
  const mockSubscribeToTier = async (tierId) => {
    setSubscribing(tierId)
    try {
      await api.mockSubscribe(tierId)
      await qc.invalidateQueries({ queryKey: ['my-subscription'] })
    } finally {
      setSubscribing(null)
    }
  }
  // Admin-only telemetry -- see backend main.get_telemetry's docstring.
  // refetchInterval keeps the TPM gauge live without a manual refresh.
  const { data: telemetry } = useQuery({
    queryKey: ['telemetry'],
    queryFn: () => api.getTelemetry(),
    enabled: !!user?.is_admin && activeTab === 'telemetry',
    refetchInterval: 5_000,
    retry: false,
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

  // Sync cursor/hasMore from query result -- but only while still on page 1
  // (extraScenarios empty). scenarioPage refetches in the background every
  // 15s while any scenario has prefab_ready===false (see the ['scenarios']
  // query above), and that refetch is always page 1; without this guard,
  // a refetch firing after the user has clicked "Load more" would silently
  // reset scenarioCursor/scenarioHasMore back to page 1's values, and the
  // next "Load more" click would re-fetch and re-append page 2, producing
  // duplicate cards with duplicate React keys.
  useEffect(() => {
    if (!scenarioPage || extraScenarios.length > 0) return
    setScenarioCursor(scenarioPage.next_cursor || null)
    setScenarioHasMore(scenarioPage.has_more || false)
  }, [scenarioPage, extraScenarios.length])

  // Hydrate savedIds from the backend on load -- without this, every fresh
  // page load shows every scenario as unsaved even though ScenarioSave rows
  // are still there, since savedIds otherwise only gets written to by
  // toggleSave() during the current session.
  useEffect(() => {
    if (!user) return
    api.getSavedScenarioIds()
      .then(({ ids }) => setSavedIds(new Set(ids)))
      .catch(logError)
  }, [user])

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
    } catch(err) { showError(err) }
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
    } catch(err) { showError(err) }
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
    } catch(err) { showError(err) }
    finally { setGenerating(false) }
  }

  async function publishScenario(id) {
    try {
      await api.publishScenario(id)
      qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch(err) { showError(err) }
  }

  async function unpublishScenario(id) {
    if (!confirm('Unpublish this scenario? It will no longer be visible to other players.')) return
    try {
      await api.unpublishScenario(id)
      qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch(err) { showError(err) }
  }

  async function reportScenario(id) {
    const reason = prompt('Why are you reporting this scenario?')
    if (!reason || !reason.trim()) return
    try {
      await api.reportScenario(id, reason.trim())
      alert('Report submitted. Thank you.')
    } catch(err) { showError(err) }
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
    } catch(err) { showError(err) }
    finally { setGenerating(false) }
  }

  async function deleteScenario(id) {
    if (!confirm('Delete this scenario?')) return
    try {
      await api.deleteScenario(id)
      setExtraScenarios(prev => prev.filter(s => s.id !== id))
      qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch(err) { showError(err) }
  }

  async function toggleSave(id, isSaved) {
    try {
      if (isSaved) { await api.unsaveScenario(id); setSavedIds(prev=>{const s=new Set(prev);s.delete(id);return s}) }
      else { await api.saveScenario(id); setSavedIds(prev=>new Set([...prev,id])) }
    } catch(err) { logError(err) }
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

  const tabs = ['persona','scenario','browse','history','billing',
                ...(user?.is_admin ? ['telemetry'] : []), 'settings']

  return (
    <div style={{display:'flex',height:'100vh',overflow:'hidden'}}>
      {errorToast && (
        <div style={{
          position:'fixed',top:16,right:16,zIndex:1000,maxWidth:360,
          background:'var(--bg-card)',border:'1px solid var(--danger)',borderRadius:10,
          padding:'12px 14px',boxShadow:'0 4px 20px rgba(0,0,0,0.35)',
          display:'flex',alignItems:'flex-start',gap:10,
        }}>
          <span style={{color:'var(--danger)',fontSize:13,flex:1,lineHeight:1.5}}>{errorToast}</span>
          <button onClick={()=>setErrorToast(null)}
            style={{background:'none',border:'none',color:'var(--text-muted)',cursor:'pointer',fontSize:16,padding:0,lineHeight:1}}>
            ×
          </button>
        </div>
      )}
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
            onReport={reportScenario}
            onUnpublish={unpublishScenario}
          />
        </div>

        {/* History */}
        <div className={`tab-panel ${activeTab==='history'?'active':''}`}>
          <div className="section-title">Recent sessions</div>
          <HistoryTab scenarios={scenarios} onReopen={openPlay}/>
        </div>

        {/* Settings */}
        {/* Billing -- mock only, no real payment ever happens here.
            No card fields anywhere by design, see backend/models.py. */}
        <div className={`tab-panel ${activeTab==='billing'?'active':''}`}>
          <div className="section-title">Plans</div>
          {tiers.length === 0 ? (
            <div style={{fontSize:13,color:'var(--text-muted)',lineHeight:1.6}}>
              Billing isn't available yet.
            </div>
          ) : (
            <>
              <div style={{fontSize:12,color:'var(--text-muted)',lineHeight:1.6,marginBottom:16}}>
                Preview only — no payment is processed, nothing is charged. This reserves your plan for when billing goes live.
              </div>
              <div style={{display:'flex',flexDirection:'column',gap:12}}>
                {tiers.map(t => {
                  const isCurrent = mySubscription?.tier_id === t.id
                  return (
                    <div key={t.id} style={{padding:'14px 16px',background:'var(--bg-card)',border:`1px solid ${isCurrent?'var(--accent)':'var(--border)'}`,borderRadius:10}}>
                      <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline'}}>
                        <div style={{fontSize:15,fontWeight:600}}>{t.name}</div>
                        <div style={{fontSize:14,color:'var(--accent)'}}>
                          {t.price_cents === 0 ? 'Free' : `$${(t.price_cents/100).toFixed(2)}/mo`}
                        </div>
                      </div>
                      <ul style={{margin:'8px 0 0',paddingLeft:18,fontSize:12,color:'var(--text-secondary)',lineHeight:1.8}}>
                        {t.features.map((f,i)=><li key={i}>{f}</li>)}
                        <li>{t.turns_per_day ? `${t.turns_per_day} turns/day` : 'Unlimited turns'}</li>
                      </ul>
                      <button
                        style={{marginTop:10}}
                        disabled={isCurrent || subscribing===t.id}
                        onClick={()=>mockSubscribeToTier(t.id)}
                      >
                        {isCurrent ? 'Current plan' : subscribing===t.id ? 'Reserving...' : 'Reserve this plan'}
                      </button>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>

        {/* Telemetry -- admin-only, see backend main.get_telemetry */}
        {user?.is_admin && (
          <div className={`tab-panel ${activeTab==='telemetry'?'active':''}`}>
            <div className="section-title">Capacity</div>
            {!telemetry ? (
              <div style={{fontSize:13,color:'var(--text-muted)'}}>Loading...</div>
            ) : (
              <>
                <div style={{padding:'14px 16px',background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:10,marginBottom:12}}>
                  <div style={{display:'flex',justifyContent:'space-between',fontSize:12,color:'var(--text-secondary)',marginBottom:6}}>
                    <span>Groq TPM budget</span>
                    <span>{telemetry.groq_tpm_used} / {telemetry.groq_tpm_limit}</span>
                  </div>
                  <div style={{height:8,background:'var(--border)',borderRadius:4,overflow:'hidden'}}>
                    <div style={{
                      height:'100%',
                      width:`${Math.min(100,(telemetry.groq_tpm_used/telemetry.groq_tpm_limit)*100)}%`,
                      background: telemetry.groq_tpm_used/telemetry.groq_tpm_limit > 0.8 ? '#e05252' : 'var(--accent)',
                      transition:'width 0.3s',
                    }}/>
                  </div>
                  <div style={{fontSize:11,color:'var(--text-muted)',marginTop:4}}>
                    Resets every minute (sliding window). Measured limit, not a guess — see rate_limiter.py.
                  </div>
                </div>

                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
                  {[
                    ['Active sessions', telemetry.active_sessions],
                    ['Prefab jobs', `${telemetry.prefab_jobs_running} / ${telemetry.prefab_jobs_max}`],
                    ['Total scenarios', telemetry.total_scenarios],
                    ['Total plays', telemetry.total_plays],
                  ].map(([label,val])=>(
                    <div key={label} style={{padding:'10px 12px',background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:10}}>
                      <div style={{fontSize:11,color:'var(--text-secondary)'}}>{label}</div>
                      <div style={{fontSize:18,fontWeight:600,marginTop:2}}>{val}</div>
                    </div>
                  ))}
                </div>

                <div className="section-title" style={{marginTop:20}}>Turn media generation</div>
                {Object.keys(telemetry.turn_media_by_status).length === 0 ? (
                  <div style={{fontSize:12,color:'var(--text-muted)'}}>No generated media yet.</div>
                ) : (
                  <div style={{display:'flex',gap:10,flexWrap:'wrap'}}>
                    {Object.entries(telemetry.turn_media_by_status).map(([status,count])=>(
                      <div key={status} style={{padding:'8px 12px',background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:8,fontSize:12}}>
                        {status}: <strong>{count}</strong>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        <div className={`tab-panel ${activeTab==='settings'?'active':''}`}>
          <div className="section-title">Engine</div>
          <div className="field">
            <label>Play model</label>
            <select value={engineModel} onChange={e=>changeModel(e.target.value)}>
              {modelOptions.length>0
                ?modelOptions.map(m=><option key={m.id} value={m.id}>{m.label}</option>)
                :<>
                  <option value="openai/gpt-oss-20b">GPT-OSS 20B — fastest</option>
                  <option value="openai/gpt-oss-120b">GPT-OSS 120B — best quality</option>
                  <option value="qwen/qwen3.6-27b">Qwen 3.6 27B — balanced</option>
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

          <div className="section-title" style={{marginTop:24}}>Account</div>
          <form onSubmit={async e => {
            e.preventDefault(); setPwMsg(null)
            if (pwNew !== pwConfirm) { setPwMsg({err:true,text:'New passwords do not match'}); return }
            setPwLoading(true)
            try {
              await api.changePassword(pwCurrent, pwNew)
              setPwCurrent(''); setPwNew(''); setPwConfirm('')
              setPwMsg({err:false,text:'Password updated.'})
            } catch(err) { setPwMsg({err:true,text:err.message}) }
            finally { setPwLoading(false) }
          }}>
            <div className="field"><label>Current password</label>
              <input type="password" value={pwCurrent} onChange={e=>setPwCurrent(e.target.value)} placeholder="••••••••" required/>
            </div>
            <div className="field"><label>New password</label>
              <input type="password" value={pwNew} onChange={e=>setPwNew(e.target.value)} placeholder="••••••••" minLength={8} required/>
            </div>
            <div className="field"><label>Confirm new password</label>
              <input type="password" value={pwConfirm} onChange={e=>setPwConfirm(e.target.value)} placeholder="••••••••" required/>
            </div>
            {pwMsg && <div style={{fontSize:13,color:pwMsg.err?'var(--danger)':'var(--accent)',marginBottom:10}}>{pwMsg.text}</div>}
            <button className="btn btn-primary" type="submit" disabled={pwLoading}>{pwLoading?'Saving…':'Change password'}</button>
          </form>
          <div style={{marginTop:20,borderTop:'1px solid var(--border)',paddingTop:16}}>
            <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:10}}>Permanently delete your account and all your data.</div>
            <button className="btn btn-ghost" style={{color:'var(--danger)',borderColor:'var(--danger)'}}
              onClick={async () => {
                if (!confirm('Delete your account? This cannot be undone.')) return
                if (!confirm('Are you sure? All scenarios and sessions will be lost.')) return
                try { await api.deleteAccount(); logout() } catch(err) { showError(err) }
              }}>
              Delete account
            </button>
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
                onUnpublish={unpublishScenario}
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
                } catch(e) { logError(e) }
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
