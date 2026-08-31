import { FormEvent, ReactNode, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Clapperboard, Film, Gauge, Layers3, Library, ListVideo, LogOut, Menu, Play, Search, Shield, Sparkles, Terminal, Ticket, Tv, Users } from 'lucide-react'
import { PlexControlCenter } from './PlexControlCenter'

type User = { id:number; discord_id:string; username:string; global_name?:string; avatar?:string; role:'SuperAdmin'|'Admin'|'Support'|'Member'; status:string }
type MediaItem = {
  id:number
  media_type:'movie'|'show'|'season'|'episode'
  title:string
  year?:number
  poster_url?:string
  backdrop_url?:string
  genres:string[]
  qualities:string[]
  summary?:string
  duration_ms?:number
  rating?:string
  actors?:string[]
  directors?:string[]
  playable?:boolean
  is_anime?:boolean
  season_number?:number
  episode_number?:number
  parent_title?:string
  grandparent_title?:string
  season_count?:number
  episode_count?:number
  seasons?:MediaItem[]
  episodes?:MediaItem[]
  library?:{id:number;title:string;type?:string;server_id?:number;server_name?:string}
  media?:Array<Record<string,unknown>>
}

type BrowseMode = 'all'|'movie'|'show'|'anime'

function errorText(body:any, fallback:string):string {
  const detail=body?.detail??body
  if(typeof detail==='string')return detail
  if(detail?.message){
    const lines=[String(detail.message)]
    if(detail.error)lines.push(String(detail.error))
    if(Array.isArray(detail.attempts))for(const attempt of detail.attempts.slice(0,6))lines.push(`${attempt.url||'route'} -> ${attempt.error||'failed'}`)
    if(Array.isArray(detail.attempts)&&detail.attempts.length>6)lines.push(`+ ${detail.attempts.length-6} more route attempts`)
    return lines.join('\n')
  }
  try{return JSON.stringify(detail)}catch{return fallback}
}

async function api<T>(path:string, init?:RequestInit):Promise<T> {
  const response = await fetch(path, { credentials:'include', headers:{'Content-Type':'application/json', ...(init?.headers||{})}, ...init })
  if (!response.ok) {
    const body = await response.json().catch(()=>({detail:`HTTP ${response.status}`}))
    throw new Error(errorText(body,`HTTP ${response.status}`))
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

function Login() {
  const [params] = useSearchParams()
  const error = params.get('error')
  return <div className="login hacker-login">
    <div className="login-art"><div className="brand">PLUMBUS<span> // PRIVATE_NET</span></div><div className="terminal-kicker"><Terminal size={14}/> ACCESS GATEWAY / CINEMA NODE</div><h2 className="login-headline">PRIVATE CINEMA.<br/>PLEX ROUTED.<br/>VRCHAT READY.</h2><p className="muted" style={{maxWidth:650}}>Encrypted server-side Plex access, invite-gated identities and temporary media routes inside one private command network.</p><div className="ascii-block">{`[ PLUMBUS NODE ]\nSTATUS : ONLINE\nAUTH   : DISCORD\nMEDIA  : PLEX\nACCESS : INVITE_ONLY`}</div></div>
    <div className="login-panel"><div className="login-box terminal-panel"><div className="terminal-panel-head"><div><span>AUTH</span> IDENTITY CHECK</div><div className="live-dot ok">SECURE</div></div><div className="terminal-body"><span className="micro-badge">PRIVATE ACCESS</span><h1>Sign in</h1><p className="muted">Existing members authenticate with Discord. New accounts require an invitation URL.</p>{error && <pre className="terminal-alert bad">{error === 'invite_required' ? 'REGISTRATION DENIED // invite required.' : 'ACCOUNT EXISTS // use normal login.'}</pre>}<a className="btn primary full-btn" href="/api/auth/discord/login">CONTINUE WITH DISCORD</a></div></div></div>
  </div>
}

function InvitePage() {
  const {token=''} = useParams(); const [state,setState]=useState<{loading:boolean;data?:any;error?:string}>({loading:true})
  useEffect(()=>{ api(`/api/invites/${encodeURIComponent(token)}/status`).then(data=>setState({loading:false,data})).catch(e=>setState({loading:false,error:e.message})) },[token])
  return <div className="login hacker-login"><div className="login-art"><div className="brand">PLUMBUS<span> // ACCESS_TOKEN</span></div><div className="terminal-kicker"><Ticket size={14}/> INVITATION HANDSHAKE</div><h2 className="login-headline">ACCESS<br/>GRANTED?</h2></div><div className="login-panel"><div className="login-box terminal-panel"><div className="terminal-panel-head"><div><span>INV</span> TOKEN INSPECTION</div></div><div className="terminal-body">{state.loading ? <div className="skeleton" style={{height:90}}/> : state.error ? <pre className="terminal-alert bad">{state.error}</pre> : <><p className="muted">Invitation role: <strong>{state.data.assigned_role}</strong>. Discord identifies your account before registration is committed.</p><a className="btn primary full-btn" href={state.data.continue_url}>ACCEPT + CONTINUE WITH DISCORD</a></>}</div></div></div></div>
}

function Shell({user,children}:{user:User;children:ReactNode}) {
  const navigate=useNavigate(); const admin=['SuperAdmin','Admin','Support'].includes(user.role)
  async function logout(){ await api('/api/auth/logout',{method:'POST'}); navigate('/login') }
  return <div className="shell"><aside className="sidebar"><div className="brand">PLUMBUS<span> // ROOT</span></div><div className="sidebar-status"><span className="status-led good"/><code>NETWORK ONLINE</code></div><div className="nav-group"><div className="nav-label">/cinema</div><NavLink className="nav-link" to="/browse"><Film size={17}/>BROWSE</NavLink><NavLink className="nav-link" to="/search"><Search size={17}/>SEARCH</NavLink><NavLink className="nav-link" to="/collections"><Library size={17}/>COLLECTIONS</NavLink></div>{admin&&<div className="nav-group"><div className="nav-label">/operations</div><NavLink className="nav-link" to="/admin"><Gauge size={17}/>DASHBOARD</NavLink>{user.role!=='Support'&&<><NavLink className="nav-link" to="/admin/users"><Users size={17}/>USERS</NavLink><NavLink className="nav-link" to="/admin/invites"><Ticket size={17}/>INVITES</NavLink></>}<NavLink className="nav-link" to="/admin/plex"><ListVideo size={17}/>PLEX NODES</NavLink><NavLink className="nav-link" to="/admin/logs"><Shield size={17}/>AUDIT LOG</NavLink></div>}</aside><main className="main"><header className="topbar"><div className="topbar-user"><button className="btn mobile-menu"><Menu size={16}/></button><span className="status-led good"/><strong>{user.global_name||user.username}</strong><span className="micro-badge">{user.role.toUpperCase()}</span></div><div className="topbar-path"><code>PLUMBUS://SESSION/{user.id}</code></div><button className="btn" onClick={logout}><LogOut size={15}/>SIGN OUT</button></header>{children}</main></div>
}

function Browse() {
  const [items,setItems]=useState<MediaItem[]>([]); const [loading,setLoading]=useState(true); const [q,setQ]=useState(''); const [mode,setMode]=useState<BrowseMode>('all')
  useEffect(()=>{const timer=setTimeout(()=>{setLoading(true);const params=new URLSearchParams();if(q)params.set('q',q);if(mode==='movie'||mode==='show')params.set('media_type',mode);if(mode==='anime')params.set('anime','true');api<{items:MediaItem[]}>(`/api/movies?${params}`).then(r=>setItems(r.items)).finally(()=>setLoading(false))},250);return()=>clearTimeout(timer)},[q,mode])
  const filters:[BrowseMode,string,ReactNode][]=[['all','ALL MEDIA',<Layers3 size={14}/>],['movie','MOVIES',<Film size={14}/>],['show','TV / SERIES',<Tv size={14}/>],['anime','ANIME',<Sparkles size={14}/>]]
  return <div className="page"><div className="hero command-hero browse-hero"><div><div className="terminal-kicker"><Terminal size={14}/> /MEDIA/INDEX</div><h1>{q?'SEARCH RESULT SET':'MEDIA NETWORK'}</h1><p className="muted" style={{maxWidth:680}}>Movies, TV series, anime, seasons and episodes indexed across every enabled Plex node.</p><div className="search-terminal"><span>&gt;</span><input className="input" value={q} onChange={e=>setQ(e.target.value)} placeholder="query title / actor / director / genre / library"/></div><div className="media-filters">{filters.map(([value,label,icon])=><button key={value} className={`media-filter ${mode===value?'active':''}`} onClick={()=>setMode(value)}>{icon}{label}</button>)}</div></div></div><div className="section-head terminal-section-head"><div><h2>{q?'QUERY_OUTPUT':'RECENTLY_ADDED'}</h2><div className="muted tiny-code">{items.length} INDEXED TITLES // {mode.toUpperCase()}</div></div></div>{loading?<div className="poster-grid">{Array.from({length:12}).map((_,i)=><div className="poster skeleton" key={i}/>)}</div>:<PosterGrid items={items}/>}</div>
}

function MediaTypeBadge({item}:{item:MediaItem}) {
  const label=item.is_anime?'ANIME':item.media_type==='show'?'SERIES':item.media_type==='movie'?'MOVIE':item.media_type.toUpperCase()
  return <span className={`poster-type ${item.is_anime?'anime':''}`}>{label}</span>
}

function PosterGrid({items}:{items:MediaItem[]}) { return <div className="poster-grid">{items.map(item=><NavLink className="poster-card" to={`/media/${item.id}`} key={item.id}><div className="poster">{item.poster_url?<img src={item.poster_url} alt=""/>:<div className="poster-empty"><Clapperboard/><span>{item.title}</span></div>}<MediaTypeBadge item={item}/><div className="poster-scanline"/></div><div className="poster-title crt-title" data-text={item.title}>{item.title}</div><div className="poster-meta">{item.year||'----'} {item.media_type==='show'&&item.episode_count?` // ${item.episode_count} EPS`:item.qualities?.length?` // ${item.qualities.join(' / ')}`:''}</div>{item.library?.server_name&&<div className="tiny-code">NODE:{item.library.server_name} // LIB:{item.library.title}</div>}</NavLink>)}</div> }

function EpisodeRow({episode}:{episode:MediaItem}) {
  const duration=episode.duration_ms?Math.round(episode.duration_ms/60000):null
  return <NavLink className="episode-row" to={`/media/${episode.id}`}><div className="episode-code">S{String(episode.season_number??0).padStart(2,'0')}E{String(episode.episode_number??0).padStart(2,'0')}</div><div className="episode-info"><strong>{episode.title}</strong><span>{episode.summary||'No episode summary supplied by Plex.'}</span></div><div className="episode-meta">{duration?`${duration} MIN`:''}{episode.qualities?.length?<small>{episode.qualities.join(' / ')}</small>:null}</div><Play size={17}/></NavLink>
}

function MediaDetail() {
  const {id}=useParams(); const [item,setItem]=useState<MediaItem|null>(null); const [play,setPlay]=useState<any>(null); const [error,setError]=useState('')
  useEffect(()=>{setItem(null);setPlay(null);setError('');api<MediaItem>(`/api/movies/${id}`).then(setItem).catch(e=>setError(e.message))},[id])
  async function createPlayback(){try{setPlay(await api(`/api/playback/movies/${id}`,{method:'POST'}))}catch(e:any){setError(e.message)}}
  if(error&&!item)return <div className="page"><pre className="terminal-alert bad">{error}</pre></div>; if(!item)return <div className="page"><div className="skeleton" style={{height:500}}/></div>
  const hours=item.duration_ms?Math.floor(item.duration_ms/3600000):0, mins=item.duration_ms?Math.round((item.duration_ms%3600000)/60000):0
  const typeLabel=item.is_anime?'ANIME':item.media_type.toUpperCase()
  return <div className="detail" style={item.backdrop_url?{backgroundImage:`url(${item.backdrop_url})`}:{}}><div className="detail-content"><div className="terminal-kicker"><Terminal size={14}/> /MEDIA/{item.id} // {typeLabel} {item.library?.server_name?`// NODE:${item.library.server_name.toUpperCase()}`:''}</div><div className="chip-row"><span className="micro-badge">{typeLabel}</span>{item.year&&<span className="micro-badge">{item.year}</span>}{item.rating&&<span className="micro-badge">RATING {item.rating}</span>}{item.season_number!=null&&<span className="micro-badge">SEASON {item.season_number}</span>}{item.episode_number!=null&&<span className="micro-badge">EPISODE {item.episode_number}</span>}{item.qualities?.map(q=><span className="micro-badge" key={q}>{q}</span>)}</div>{item.grandparent_title&&<div className="tiny-code detail-parent">SERIES::{item.grandparent_title} // {item.parent_title||''}</div>}<h1>{item.title}</h1><p className="muted movie-summary">{item.summary}</p>{item.duration_ms&&<p className="tiny-code">RUNTIME:{hours?`${hours}H_`:''}{mins}M {item.genres?.length?` // TAGS:${item.genres.join(',').toUpperCase()}`:''}</p>}{item.playable&&<div className="console-actions"><button className="btn primary" onClick={createPlayback}><Play size={16}/>GENERATE PLAYBACK ROUTE</button></div>}{item.media_type==='show'&&<div className="series-index"><div className="section-head terminal-section-head"><div><h2>SEASON_INDEX</h2><div className="muted tiny-code">{item.season_count||0} SEASONS // {item.episode_count||0} EPISODES</div></div></div>{item.seasons?.map(season=><section className="season-block terminal-panel" key={season.id}><div className="terminal-panel-head"><div><span>S{String(season.season_number??0).padStart(2,'0')}</span> {season.title.toUpperCase()}</div><div className="live-dot">{season.episodes?.length||0} EPS</div></div><div className="episode-list">{season.episodes?.map(ep=><EpisodeRow episode={ep} key={ep.id}/>)}</div></section>)}</div>}{item.media_type==='season'&&<div className="series-index"><div className="section-head terminal-section-head"><h2>EPISODE_INDEX</h2></div><div className="episode-list terminal-panel">{item.episodes?.map(ep=><EpisodeRow episode={ep} key={ep.id}/>)}</div></div>}{error&&<pre className="terminal-alert bad">{error}</pre>}{play&&<div className="terminal-panel playback-panel"><div className="terminal-panel-head"><div><span>URL</span> TEMPORARY MEDIA ROUTE</div><div className="live-dot ok">ACTIVE</div></div><div className="terminal-body"><div className="label">EXPIRES {new Date(play.expires_at).toLocaleString()}</div><input className="input" readOnly value={play.playback_url} onFocus={e=>e.currentTarget.select()}/><div className="console-actions"><button className="btn" onClick={()=>navigator.clipboard.writeText(play.playback_url)}>COPY URL</button><a className="btn" href={play.playback_url}>OPEN STREAM</a></div></div></div>}</div></div>
}

function AdminDashboard() {
  const [users,setUsers]=useState<any[]>([]),[invites,setInvites]=useState<any[]>([]),[scans,setScans]=useState<any[]>([])
  useEffect(()=>{Promise.allSettled([api<any[]>('/api/admin/users'),api<any[]>('/api/admin/invites'),api<any[]>('/api/plex/scans')]).then(([u,i,s])=>{if(u.status==='fulfilled')setUsers(u.value);if(i.status==='fulfilled')setInvites(i.value);if(s.status==='fulfilled')setScans(s.value)})},[])
  const activeInvites=invites.filter(x=>x.status==='Active').length
  return <div className="page hacker-page"><div className="command-hero compact"><div><div className="terminal-kicker"><Terminal size={14}/> /OPS/OVERVIEW</div><h1>OPERATIONS CONSOLE</h1><p>Identity, invitation, Plex indexing and network activity at a glance.</p></div></div><div className="grid-stats"><div className="stat"><span>USERS</span><strong>{users.length}</strong></div><div className="stat"><span>ACTIVE INVITES</span><strong>{activeInvites}</strong></div><div className="stat"><span>SCAN JOBS</span><strong>{scans.length}</strong></div><div className="stat"><span>LAST JOB</span><strong className="small-stat">{scans[0]?.status?.toUpperCase()||'NONE'}</strong></div></div><div className="section-head terminal-section-head"><h2>RECENT_SCAN_ACTIVITY</h2></div><ScanTable scans={scans}/></div>
}

function InviteAdmin({user}:{user:User}) {
  const [rows,setRows]=useState<any[]>([]),[created,setCreated]=useState<any>(null),[error,setError]=useState(''); const [form,setForm]=useState({label:'',expires_in_minutes:1440,max_uses:1,assigned_role:'Member'})
  const load=()=>api<any[]>('/api/admin/invites').then(setRows).catch(e=>setError(e.message)); useEffect(()=>{void load()},[])
  async function submit(e:FormEvent){e.preventDefault();try{const result=await api('/api/admin/invites',{method:'POST',body:JSON.stringify(form)});setCreated(result);load()}catch(e:any){setError(e.message)}}
  async function revoke(id:number){await api(`/api/admin/invites/${id}/revoke`,{method:'POST'});load()}
  return <div className="page hacker-page"><div className="section-head terminal-section-head"><div><h2>INVITATION_TOKENS</h2><div className="muted tiny-code">RAW TOKEN MATERIAL IS RETURNED ONCE.</div></div></div>{error&&<pre className="terminal-alert bad">{error}</pre>}<div className="split"><div className="terminal-panel"><div className="terminal-panel-head"><div><span>LIST</span> TOKEN HISTORY</div></div><div className="table-wrap"><table className="terminal-table"><thead><tr><th>Label</th><th>Role</th><th>Uses</th><th>Expires</th><th>Status</th><th/></tr></thead><tbody>{rows.map(x=><tr key={x.id}><td>{x.label||`Invite #${x.id}`}</td><td>{x.assigned_role}</td><td>{x.use_count}/{x.max_uses??'∞'}</td><td>{x.expires_at?new Date(x.expires_at).toLocaleString():'NEVER'}</td><td><span className={`micro-badge ${x.status==='Active'?'good':x.status==='Revoked'?'bad':'warn'}`}>{x.status.toUpperCase()}</span></td><td>{x.status==='Active'&&<button className="btn danger" onClick={()=>revoke(x.id)}>REVOKE</button>}</td></tr>)}</tbody></table></div></div><form className="terminal-panel" onSubmit={submit}><div className="terminal-panel-head"><div><span>NEW</span> MINT INVITATION</div></div><div className="terminal-body"><label className="label">LABEL</label><input className="input" value={form.label} onChange={e=>setForm({...form,label:e.target.value})}/><div className="form-grid"><div><label className="label">TTL</label><select className="select" value={form.expires_in_minutes} onChange={e=>setForm({...form,expires_in_minutes:Number(e.target.value)})}><option value={60}>1 hour</option><option value={360}>6 hours</option><option value={1440}>24 hours</option><option value={4320}>3 days</option><option value={10080}>7 days</option><option value={43200}>30 days</option></select></div><div><label className="label">MAX USES</label><select className="select" value={form.max_uses} onChange={e=>setForm({...form,max_uses:Number(e.target.value)})}>{[1,5,10,25].map(x=><option key={x}>{x}</option>)}</select></div></div><label className="label">ROLE</label><select className="select" value={form.assigned_role} onChange={e=>setForm({...form,assigned_role:e.target.value})}><option>Member</option><option>Support</option>{user.role==='SuperAdmin'&&<option>Admin</option>}</select><button className="btn primary full-btn">CREATE SECURE INVITE</button>{created&&<div><label className="label">COPY NOW // CANNOT BE RECOVERED</label><input className="input" readOnly value={created.invite_url} onFocus={e=>e.currentTarget.select()}/><button type="button" className="btn" onClick={()=>navigator.clipboard.writeText(created.invite_url)}>COPY TOKEN URL</button></div>}</div></form></div></div>
}

function UsersAdmin() {
 const [rows,setRows]=useState<any[]>([]),[error,setError]=useState(''); const load=()=>api<any[]>('/api/admin/users').then(setRows).catch(e=>setError(e.message)); useEffect(()=>{void load()},[])
 async function status(id:number,value:string){try{await api(`/api/admin/users/${id}/status`,{method:'PATCH',body:JSON.stringify({status:value})});load()}catch(e:any){setError(e.message)}}
 return <div className="page hacker-page"><div className="section-head terminal-section-head"><div><h2>IDENTITY_REGISTRY</h2><div className="muted tiny-code">DISCORD-LINKED USERS + ACCESS STATE</div></div></div>{error&&<pre className="terminal-alert bad">{error}</pre>}<div className="terminal-panel table-wrap"><table className="terminal-table"><thead><tr><th>User</th><th>Discord ID</th><th>Role</th><th>Status</th><th>Joined</th><th>Last login</th><th>Actions</th></tr></thead><tbody>{rows.map(x=><tr key={x.id}><td><strong>{x.global_name||x.username}</strong></td><td><code>{x.discord_id}</code></td><td><span className="micro-badge">{x.role.toUpperCase()}</span></td><td>{x.status}</td><td>{new Date(x.joined).toLocaleDateString()}</td><td>{x.last_login?new Date(x.last_login).toLocaleString():'NEVER'}</td><td className="row-actions">{x.status==='Active'?<button className="btn danger" onClick={()=>status(x.id,'Suspended')}>SUSPEND</button>:<button className="btn" onClick={()=>status(x.id,'Active')}>UNSUSPEND</button>}<button className="btn danger" onClick={()=>status(x.id,'Banned')}>BAN</button></td></tr>)}</tbody></table></div></div>
}

function ScanTable({scans}:{scans:any[]}) { return <div className="terminal-panel table-wrap"><table className="terminal-table"><thead><tr><th>Job</th><th>Node / Library</th><th>Mode</th><th>Status</th><th>Scanned</th><th>+ / ~ / −</th><th>Finished / Error</th></tr></thead><tbody>{scans.map(x=><tr key={x.id}><td>#{x.id}</td><td>{x.server_name||x.server_id||'—'}<div className="tiny-code">{x.library_name||x.library_id||'—'}</div></td><td>{x.mode}</td><td><span className={`micro-badge ${x.status==='completed'?'good':x.status==='failed'?'bad':'warn'}`}>{String(x.status).toUpperCase()}</span></td><td>{x.items_scanned}</td><td>{x.items_added} / {x.items_updated} / {x.items_removed}</td><td>{x.last_error?<span className="error-line">{x.last_error}</span>:x.finished_at?new Date(x.finished_at).toLocaleString():'—'}</td></tr>)}</tbody></table></div> }

function Logs() { const [rows,setRows]=useState<any[]>([]);useEffect(()=>{api<any[]>('/api/audit').then(setRows)},[]);return <div className="page hacker-page"><div className="section-head terminal-section-head"><h2>AUDIT_STREAM</h2></div><div className="terminal-panel table-wrap"><table className="terminal-table"><thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Target</th><th>IP</th></tr></thead><tbody>{rows.map(x=><tr key={x.id}><td>{new Date(x.created_at).toLocaleString()}</td><td><code>{x.event}</code></td><td>{x.actor_user_id??'system'}</td><td>{x.target_type?`${x.target_type}:${x.target_id}`:'—'}</td><td>{x.ip||'sanitized'}</td></tr>)}</tbody></table></div></div> }

function ProtectedApp() {
 const [user,setUser]=useState<User|null|undefined>(undefined)
 useEffect(()=>{api<User>('/api/auth/me').then(setUser).catch(()=>setUser(null))},[])
 if(user===undefined)return <div className="page"><div className="skeleton" style={{height:400}}/></div>; if(user===null)return <Navigate to="/login" replace/>
 return <Shell user={user}><Routes><Route path="/browse" element={<Browse/>}/><Route path="/search" element={<Browse/>}/><Route path="/collections" element={<Browse/>}/><Route path="/media/:id" element={<MediaDetail/>}/><Route path="/movie/:id" element={<MediaDetail/>}/><Route path="/admin" element={<AdminDashboard/>}/><Route path="/admin/invites" element={<InviteAdmin user={user}/>}/><Route path="/admin/users" element={<UsersAdmin/>}/><Route path="/admin/plex" element={<PlexControlCenter user={user}/>}/><Route path="/admin/logs" element={<Logs/>}/><Route path="*" element={<Navigate to="/browse" replace/>}/></Routes></Shell>
}

export default function App(){return <Routes><Route path="/login" element={<Login/>}/><Route path="/invite/:token" element={<InvitePage/>}/><Route path="/*" element={<ProtectedApp/>}/></Routes>}