import { FormEvent, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, ChevronLeft, ChevronRight, Database, ExternalLink, KeyRound, Link2, Loader2, LockKeyhole, MonitorCog, Play, RefreshCw, ScanLine, Server, Settings2, ShieldCheck, Terminal, Wifi, WifiOff } from 'lucide-react'

type SetupConfig = {
  site:{app_url:string;site_name:string}
  discord:{client_id:string;client_secret_configured:boolean;owner_discord_id:string;redirect_uri:string}
  plex_linked:boolean
  playback:{preferred_video_codec:string;preferred_resolution:string;max_stream_bitrate_kbps:number;allow_plex_transcoding:boolean}
  readiness:{ready:boolean;checks:Record<string,boolean>}
}
type PlexConnection={uri:string;local:boolean;relay:boolean;protocol?:string;reachable?:boolean;error?:string}
type PlexServerNode={name:string;client_identifier:string;owned:boolean;connections:PlexConnection[]}
type LibraryNode={key:string;title:string;type:string;enabled:boolean}

function message(body:any,fallback:string){
  const detail=body?.detail??body
  if(typeof detail==='string')return detail
  if(detail?.message){
    const lines=[String(detail.message)]
    if(detail.error)lines.push(String(detail.error))
    if(Array.isArray(detail.attempts))for(const x of detail.attempts)lines.push(`${x.url||'route'} -> ${x.error||'failed'}`)
    return lines.join('\n')
  }
  try{return JSON.stringify(detail)}catch{return fallback}
}
async function api<T>(path:string,init?:RequestInit):Promise<T>{
  const r=await fetch(path,{credentials:'include',headers:{'Content-Type':'application/json',...(init?.headers||{})},...init})
  if(!r.ok){const body=await r.json().catch(()=>({detail:`HTTP ${r.status}`}));throw new Error(message(body,`HTTP ${r.status}`))}
  return r.status===204?(undefined as T):r.json()
}
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms))

const steps=[
  ['claim','CLAIM INSTALL'],
  ['site','SITE IDENTITY'],
  ['discord','DISCORD AUTH'],
  ['plex','PLEX NETWORK'],
  ['libraries','LIBRARY MAP'],
  ['playback','PLAYBACK'],
  ['finish','READINESS'],
] as const

type Step=typeof steps[number][0]

export function SetupWizard(){
  const [step,setStep]=useState<Step>('claim')
  const [claimed,setClaimed]=useState(false)
  const [complete,setComplete]=useState(false)
  const [code,setCode]=useState('')
  const [config,setConfig]=useState<SetupConfig|null>(null)
  const [site,setSite]=useState({app_url:window.location.origin,site_name:'Plumbus Cinema'})
  const [discord,setDiscord]=useState({client_id:'',client_secret:'',owner_discord_id:''})
  const [servers,setServers]=useState<PlexServerNode[]>([])
  const [libraries,setLibraries]=useState<LibraryNode[]>([])
  const [selectedLibraries,setSelectedLibraries]=useState<string[]>([])
  const [playback,setPlayback]=useState({preferred_video_codec:'h264',preferred_resolution:'1080p',max_stream_bitrate_kbps:20000,allow_plex_transcoding:false})
  const [busy,setBusy]=useState('')
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const idx=steps.findIndex(x=>x[0]===step)

  async function loadConfig(){
    const c=await api<SetupConfig>('/api/setup/config')
    setConfig(c);setSite(c.site);setDiscord(d=>({...d,client_id:c.discord.client_id||'',owner_discord_id:c.discord.owner_discord_id||''}));setPlayback(c.playback)
    return c
  }

  useEffect(()=>{
    api<{completed:boolean;claimed:boolean}>('/api/setup/status').then(async s=>{
      if(s.completed){setComplete(true);window.location.href='/';return}
      setClaimed(s.claimed)
      if(s.claimed){await loadConfig();setStep('site')}
    }).catch(e=>setError(e.message))
  },[])

  async function claim(e:FormEvent){e.preventDefault();setBusy('claim');setError('');try{await api('/api/setup/claim',{method:'POST',body:JSON.stringify({code})});setClaimed(true);await loadConfig();setStep('site');setNotice('INSTALL CLAIM ACCEPTED // session unlocked')}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function saveSite(e:FormEvent){e.preventDefault();setBusy('site');setError('');try{const r=await api<any>('/api/setup/site',{method:'PUT',body:JSON.stringify(site)});setSite(r);setNotice('SITE IDENTITY SAVED');setStep('discord')}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function saveDiscord(e:FormEvent){e.preventDefault();setBusy('discord');setError('');try{const r=await api<any>('/api/setup/discord',{method:'PUT',body:JSON.stringify(discord)});setNotice(`DISCORD CONFIG SAVED // redirect ${r.redirect_uri}`);await loadConfig();setStep('plex')}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function signInPlex(){setBusy('plex-auth');setError('');try{const s=await api<{pin_id:number;auth_url:string}>('/api/setup/plex/sign-in',{method:'POST'});window.open(s.auth_url,'plex-auth','popup,width=900,height=760');for(let i=0;i<60;i++){await sleep(2000);const p=await api<{authenticated:boolean}>(`/api/setup/plex/sign-in/${s.pin_id}`);if(p.authenticated){setNotice('PLEX ACCOUNT LINKED // probing server routes');await refreshServers();await loadConfig();return}}throw new Error('Plex sign-in timed out. Start it again.')}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function refreshServers(){setBusy('servers');setError('');try{const rows=await api<PlexServerNode[]>('/api/setup/plex/servers');setServers(rows);setNotice(`DISCOVERED ${rows.length} PLEX SERVER NODE${rows.length===1?'':'S'}`)}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function tryRoute(node:PlexServerNode,route:PlexConnection){setBusy(`route-${node.client_identifier}-${route.uri}`);setError('');try{const r=await api<any>('/api/setup/plex/server',{method:'POST',body:JSON.stringify({client_identifier:node.client_identifier,connection_uri:route.uri})});setNotice(`CONNECTED // ${r.name} via ${r.connection}`);await loadLibraries();setStep('libraries')}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function loadLibraries(){setBusy('libraries');setError('');try{const rows=await api<LibraryNode[]>('/api/setup/plex/libraries');setLibraries(rows);setSelectedLibraries(rows.filter(x=>x.enabled).map(x=>x.key));setNotice(`LIBRARY MAP LOADED // ${rows.length} found`)}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function saveLibraries(){setBusy('save-libs');setError('');try{await api('/api/setup/plex/libraries',{method:'PUT',body:JSON.stringify({enabled_keys:selectedLibraries})});setNotice(`${selectedLibraries.length} LIBRARIES ENABLED`);setStep('playback')}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function savePlayback(e:FormEvent){e.preventDefault();setBusy('playback');setError('');try{await api('/api/setup/playback',{method:'PUT',body:JSON.stringify(playback)});await loadConfig();setNotice('PLAYBACK POLICY SAVED');setStep('finish')}catch(e:any){setError(e.message)}finally{setBusy('')}}
  async function finish(){setBusy('finish');setError('');try{const ready=await api<any>('/api/setup/readiness');setConfig(c=>c?{...c,readiness:ready}:c);if(!ready.ready)throw new Error(`Readiness failed: ${Object.entries(ready.checks).filter(([,ok])=>!ok).map(([k])=>k).join(', ')}`);const result=await api<any>('/api/setup/complete',{method:'POST'});setComplete(true);setNotice('SETUP COMPLETE // forwarding to SuperAdmin bootstrap');window.location.href=result.next}catch(e:any){setError(e.message)}finally{setBusy('')}}

  const readiness=config?.readiness?.checks||{}
  const selectedCount=selectedLibraries.length
  const canNext=claimed

  return <div className="setup-shell">
    <aside className="setup-rail">
      <div className="brand">PLUMBUS<span> // INSTALL</span></div>
      <div className="sidebar-status"><span className="status-led good"/><code>FIRST_RUN MODE</code></div>
      <div className="setup-nav">{steps.map(([key,label],i)=><button key={key} className={`${step===key?'active':''} ${i<idx?'done':''}`} disabled={!claimed&&key!=='claim'} onClick={()=>claimed&&setStep(key)}><span>{String(i+1).padStart(2,'0')}</span>{label}</button>)}</div>
      <div className="setup-rail-note">INFRASTRUCTURE SECRETS STAY IN DOCKER.<br/><br/>DISCORD + PLEX CONFIGURATION LIVES HERE.</div>
    </aside>
    <main className="setup-main">
      <div className="setup-top"><div className="terminal-kicker"><Terminal size={14}/> PLUMBUS://FIRST_RUN/{step.toUpperCase()}</div><div className="live-dot ok">{complete?'COMPLETE':'CONFIG MODE'}</div></div>
      {error&&<div className="terminal-alert bad"><pre>{error}</pre></div>}{notice&&<div className="terminal-alert good"><pre>{notice}</pre></div>}

      {step==='claim'&&<section className="setup-stage"><div className="stage-copy"><KeyRound size={28}/><h1>CLAIM THIS INSTALL</h1><p>Enter the one-time code printed by the Plumbus backend container. This prevents somebody who finds a fresh public deployment from taking ownership.</p></div><form className="terminal-panel setup-form" onSubmit={claim}><div className="terminal-panel-head"><div><span>AUTH</span> INSTALL CLAIM</div></div><div className="terminal-body"><label className="label">FIRST-RUN SETUP CODE</label><input autoFocus className="input setup-code" value={code} onChange={e=>setCode(e.target.value.toUpperCase())} placeholder="XXXXXXXXXX"/><button className="btn primary" disabled={busy==='claim'}>{busy==='claim'?<Loader2 className="spin" size={15}/>:<LockKeyhole size={15}/>}CLAIM INSTALLATION</button><p className="mono-copy">Dockge → backend → Logs → look for <strong>PLUMBUS FIRST-RUN SETUP CODE</strong>.</p></div></form></section>}

      {step==='site'&&<section className="setup-stage"><div className="stage-copy"><MonitorCog size={28}/><h1>SITE IDENTITY</h1><p>Set the public URL people will actually use. This becomes the base for Discord callbacks and generated playback URLs.</p></div><form className="terminal-panel setup-form" onSubmit={saveSite}><div className="terminal-panel-head"><div><span>NET</span> PUBLIC ENDPOINT</div></div><div className="terminal-body"><label className="label">PUBLIC APP URL</label><input className="input" value={site.app_url} onChange={e=>setSite({...site,app_url:e.target.value})} placeholder="https://cinema.example.com"/><label className="label">SITE NAME</label><input className="input" value={site.site_name} onChange={e=>setSite({...site,site_name:e.target.value})}/><button className="btn primary">SAVE + CONTINUE <ChevronRight size={14}/></button></div></form></section>}

      {step==='discord'&&<section className="setup-stage"><div className="stage-copy"><ShieldCheck size={28}/><h1>DISCORD OAUTH</h1><p>Create a Discord application, copy its client ID and secret below, then add the generated callback URL in the Discord Developer Portal.</p></div><form className="terminal-panel setup-form" onSubmit={saveDiscord}><div className="terminal-panel-head"><div><span>AUTH</span> DISCORD IDENTITY</div></div><div className="terminal-body"><label className="label">CLIENT ID</label><input className="input" value={discord.client_id} onChange={e=>setDiscord({...discord,client_id:e.target.value})}/><label className="label">CLIENT SECRET</label><input className="input" type="password" value={discord.client_secret} onChange={e=>setDiscord({...discord,client_secret:e.target.value})} placeholder={config?.discord.client_secret_configured?'Already configured — leave blank to keep':'Required'}/><label className="label">OWNER DISCORD USER ID</label><input className="input" value={discord.owner_discord_id} onChange={e=>setDiscord({...discord,owner_discord_id:e.target.value})}/><label className="label">REDIRECT URI</label><div className="copy-line"><code>{config?.discord.redirect_uri||`${site.app_url}/api/auth/discord/callback`}</code><button type="button" className="btn" onClick={()=>navigator.clipboard.writeText(config?.discord.redirect_uri||`${site.app_url}/api/auth/discord/callback`)}>COPY</button></div><button className="btn primary">SAVE + CONTINUE <ChevronRight size={14}/></button></div></form></section>}

      {step==='plex'&&<section className="setup-stage wide"><div className="stage-copy"><Server size={28}/><h1>PLEX NETWORK</h1><p>Authenticate to Plex, discover every server on the account, then try any advertised route. A dead LAN route does not block you from trying its remote, direct or relay alternatives.</p><div className="stack-actions"><button className="btn primary" onClick={signInPlex} disabled={busy==='plex-auth'}><Link2 size={14}/>{config?.plex_linked?'RE-LINK PLEX':'SIGN IN TO PLEX'}</button><button className="btn" onClick={refreshServers} disabled={!config?.plex_linked||busy==='servers'}><RefreshCw size={14}/>DISCOVER SERVERS</button></div></div><div className="setup-node-list">{servers.length===0?<div className="terminal-panel empty-terminal">NO PLEX SERVERS LOADED YET.</div>:servers.map(node=><div className="terminal-panel" key={node.client_identifier}><div className="terminal-panel-head"><div><span>NODE</span> {node.name}</div><div className="live-dot">{node.owned?'OWNED':'SHARED'}</div></div><div className="terminal-body"><code className="tiny-code">{node.client_identifier}</code><div className="route-list">{node.connections.map(route=><div className={`route-row ${route.reachable?'reachable':'unreachable'}`} key={route.uri}><div className="route-icon">{route.reachable?<Wifi size={15}/>:<WifiOff size={15}/>}</div><div className="route-info"><code>{route.uri}</code><small>{route.local?'LOCAL':'REMOTE'} // {route.relay?'RELAY':'DIRECT'} // {route.protocol||'?'}</small>{route.error&&<small className="error-line">{route.error}</small>}</div><button className="btn" onClick={()=>tryRoute(node,route)} disabled={busy===`route-${node.client_identifier}-${route.uri}`}>TRY CONNECTION</button></div>)}</div></div></div>)}</div></section>}

      {step==='libraries'&&<section className="setup-stage wide"><div className="stage-copy"><Database size={28}/><h1>LIBRARY MAP</h1><p>Select every Plex library Plumbus should index. You can add more servers and change library visibility later from the Plex Node Control page.</p><div className="stack-actions"><button className="btn" onClick={loadLibraries}><RefreshCw size={14}/>REFRESH</button><button className="btn primary" disabled={selectedCount===0||busy==='save-libs'} onClick={saveLibraries}>ENABLE {selectedCount} + CONTINUE <ChevronRight size={14}/></button></div></div><div className="terminal-panel"><div className="terminal-panel-head"><div><span>LIB</span> AVAILABLE SECTIONS</div><div className="live-dot">{libraries.length} FOUND</div></div><div className="setup-library-grid">{libraries.map(lib=>{const on=selectedLibraries.includes(lib.key);return <button key={lib.key} className={`setup-library ${on?'selected':''}`} onClick={()=>setSelectedLibraries(on?selectedLibraries.filter(x=>x!==lib.key):[...selectedLibraries,lib.key])}><Database size={18}/><strong>{lib.title}</strong><code>{lib.type.toUpperCase()} // KEY:{lib.key}</code><span>{on?'[X] INDEX':'[ ] SKIP'}</span></button>})}</div></div></section>}

      {step==='playback'&&<section className="setup-stage"><div className="stage-copy"><Play size={28}/><h1>PLAYBACK POLICY</h1><p>Set sensible defaults for VRChat playback. Direct play remains preferred; Plex transcoding is optional.</p></div><form className="terminal-panel setup-form" onSubmit={savePlayback}><div className="terminal-panel-head"><div><span>AV</span> MEDIA POLICY</div></div><div className="terminal-body"><div className="form-grid"><div><label className="label">PREFERRED VIDEO CODEC</label><select className="select" value={playback.preferred_video_codec} onChange={e=>setPlayback({...playback,preferred_video_codec:e.target.value})}><option value="h264">H.264 / AVC</option><option value="hevc">HEVC / H.265</option></select></div><div><label className="label">PREFERRED RESOLUTION</label><select className="select" value={playback.preferred_resolution} onChange={e=>setPlayback({...playback,preferred_resolution:e.target.value})}><option>720p</option><option>1080p</option><option>1440p</option><option>4K</option></select></div></div><label className="label">MAX STREAM BITRATE (KBPS)</label><input className="input" type="number" min="500" max="200000" value={playback.max_stream_bitrate_kbps} onChange={e=>setPlayback({...playback,max_stream_bitrate_kbps:Number(e.target.value)})}/><label className="check-line"><input type="checkbox" checked={playback.allow_plex_transcoding} onChange={e=>setPlayback({...playback,allow_plex_transcoding:e.target.checked})}/><span>ALLOW PLEX TRANSCODING WHEN DIRECT PLAY IS NOT SUITABLE</span></label><button className="btn primary">SAVE + CONTINUE <ChevronRight size={14}/></button></div></form></section>}

      {step==='finish'&&<section className="setup-stage"><div className="stage-copy"><Settings2 size={28}/><h1>READINESS CHECK</h1><p>Plumbus will not complete setup until the site, Discord identity, Plex account, selected server and libraries are ready.</p></div><div className="terminal-panel setup-form"><div className="terminal-panel-head"><div><span>CHK</span> SYSTEM READINESS</div></div><div className="terminal-body"><div className="readiness-grid">{Object.entries(readiness).map(([key,ok])=><div key={key} className={ok?'ready':'not-ready'}>{ok?<CheckCircle2 size={15}/>:<ScanLine size={15}/>}<code>{key.toUpperCase()}</code><span>{ok?'PASS':'WAIT'}</span></div>)}</div><button className="btn" onClick={()=>loadConfig()}><RefreshCw size={14}/>RECHECK</button><button className="btn primary" onClick={finish} disabled={busy==='finish'}>{busy==='finish'?<Loader2 className="spin" size={15}/>:<ShieldCheck size={15}/>}COMPLETE SETUP + BOOTSTRAP SUPERADMIN</button></div></div></section>}

      {claimed&&step!=='claim'&&<div className="setup-footer"><button className="btn" disabled={idx<=1} onClick={()=>setStep(steps[Math.max(1,idx-1)][0])}><ChevronLeft size={14}/>BACK</button><div className="tiny-code">STEP {idx+1}/{steps.length} // CONFIG SESSION ACTIVE</div>{canNext&&idx<steps.length-1?<button className="btn" onClick={()=>setStep(steps[idx+1][0])}>NEXT VIEW<ChevronRight size={14}/></button>:<span/>}</div>}
    </main>
  </div>
}
