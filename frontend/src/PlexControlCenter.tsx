import { useEffect, useMemo, useState } from 'react'
import { Activity, Cable, CheckCircle2, Database, Globe2, Link2, Loader2, Play, Power, RefreshCw, ScanLine, Server, ShieldAlert, Terminal, Unplug, Wifi, WifiOff } from 'lucide-react'

type UserLike = { role: 'SuperAdmin'|'Admin'|'Support'|'Member' }
type SavedServer = {
  id:number
  name:string
  identifier?:string
  version?:string
  base_url:string
  enabled:boolean
  active:boolean
  library_count:number
  enabled_library_count:number
  token_configured:boolean
}
type AccountConnection = { uri:string; local:boolean; relay:boolean; protocol?:string; reachable?:boolean; error?:string }
type AccountServer = { name:string; client_identifier:string; owned:boolean; saved_server_id?:number|null; connections:AccountConnection[] }
type LibraryRow = { id:number; server_id:number; server_name?:string; plex_key:string; title:string; type:string; enabled:boolean; visible_to_members:boolean; last_scan_at?:string }
type ScanRow = { id:number; library_id?:number; library_name?:string; server_id?:number; server_name?:string; mode:string; status:string; started_at?:string; finished_at?:string; last_error?:string; items_scanned:number; items_added:number; items_updated:number; items_removed:number }

function errorText(body:any, fallback:string):string {
  const detail = body?.detail ?? body
  if (typeof detail === 'string') return detail
  if (detail?.message) {
    const parts = [String(detail.message)]
    if (detail.error) parts.push(String(detail.error))
    if (Array.isArray(detail.attempts)) {
      for (const attempt of detail.attempts) parts.push(`${attempt.url || 'route'} -> ${attempt.error || 'failed'}`)
    }
    return parts.join('\n')
  }
  try { return JSON.stringify(detail) } catch { return fallback }
}

async function api<T>(path:string, init?:RequestInit):Promise<T> {
  const response = await fetch(path, {
    credentials:'include',
    headers:{'Content-Type':'application/json', ...(init?.headers||{})},
    ...init,
  })
  if (!response.ok) {
    const body = await response.json().catch(()=>({detail:`HTTP ${response.status}`}))
    throw new Error(errorText(body, `HTTP ${response.status}`))
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

const sleep=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms))

export function PlexControlCenter({user}:{user:UserLike}) {
  const [servers,setServers]=useState<SavedServer[]>([])
  const [accountServers,setAccountServers]=useState<AccountServer[]>([])
  const [libraries,setLibraries]=useState<LibraryRow[]>([])
  const [scans,setScans]=useState<ScanRow[]>([])
  const [selectedServer,setSelectedServer]=useState<number|null>(null)
  const [accountLinked,setAccountLinked]=useState(false)
  const [busy,setBusy]=useState('')
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [manual,setManual]=useState({base_url:'',token:''})
  const superadmin=user.role==='SuperAdmin'
  const canAdmin=user.role==='SuperAdmin'||user.role==='Admin'

  const selected=servers.find(x=>x.id===selectedServer) || servers.find(x=>x.active) || servers[0]
  const visibleLibraries=useMemo(()=>selected?libraries.filter(x=>x.server_id===selected.id):libraries,[libraries,selected])
  const selectedScans=useMemo(()=>selected?scans.filter(x=>x.server_id===selected.id):scans,[scans,selected])

  async function loadBase() {
    setError('')
    const [serverResult,libraryResult,scanResult]=await Promise.allSettled([
      api<SavedServer[]>('/api/plex/servers'),
      api<LibraryRow[]>('/api/plex/libraries'),
      api<ScanRow[]>('/api/plex/scans'),
    ])
    if(serverResult.status==='fulfilled') {
      setServers(serverResult.value)
      setSelectedServer(current=>current ?? serverResult.value.find(x=>x.active)?.id ?? serverResult.value[0]?.id ?? null)
    } else setError(serverResult.reason?.message||'Unable to load Plex servers')
    if(libraryResult.status==='fulfilled') setLibraries(libraryResult.value)
    if(scanResult.status==='fulfilled') setScans(scanResult.value)
    if(superadmin) {
      try { const status=await api<{linked:boolean}>('/api/plex/account/status'); setAccountLinked(status.linked) } catch { setAccountLinked(false) }
    }
  }

  async function refreshAccountServers() {
    if(!superadmin)return
    setBusy('account-servers');setError('')
    try {
      const rows=await api<AccountServer[]>('/api/plex/account/servers')
      setAccountServers(rows)
      setNotice(`Plex account returned ${rows.length} server node${rows.length===1?'':'s'}.`)
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  useEffect(()=>{void loadBase()},[])

  async function signInPlex() {
    if(!superadmin)return
    setBusy('plex-signin');setError('');setNotice('Opening Plex authentication…')
    try {
      const start=await api<{pin_id:number;auth_url:string}>('/api/plex/account/sign-in',{method:'POST'})
      window.open(start.auth_url,'plex-auth','popup,width=900,height=760')
      for(let attempt=0;attempt<60;attempt++) {
        await sleep(2000)
        const poll=await api<{authenticated:boolean}>(`/api/plex/account/sign-in/${start.pin_id}`)
        if(poll.authenticated) {
          setAccountLinked(true)
          setNotice('PLEX AUTHENTICATION ACCEPTED // discovering nodes…')
          await refreshAccountServers()
          return
        }
      }
      throw new Error('Plex authentication timed out. Start the sign-in again.')
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function connectAccountServer(node:AccountServer,uri?:string) {
    setBusy(`connect-${node.client_identifier}-${uri||'auto'}`);setError('');setNotice('')
    try {
      const result=await api<any>('/api/plex/account/auto-connect',{
        method:'POST',
        body:JSON.stringify({client_identifier:node.client_identifier,preferred_uri:uri||null,set_active:true}),
      })
      setNotice(`CONNECTED // ${result.server?.name||node.name} via ${result.server?.base_url||uri||'automatic route'}`)
      await loadBase()
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function testServer(server:SavedServer) {
    setBusy(`test-${server.id}`);setError('');setNotice('')
    try {
      const result=await api<any>(`/api/plex/servers/${server.id}/test`,{method:'POST'})
      if(!result.connected) throw new Error(`${result.base_url}\n${result.error||'Connection failed'}`)
      setNotice(`NODE ${server.id} ONLINE // ${result.name||server.name} // ${result.library_count} libraries detected`)
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function activateServer(server:SavedServer) {
    setBusy(`activate-${server.id}`);setError('')
    try {
      await api(`/api/plex/servers/${server.id}/activate`,{method:'POST'})
      setSelectedServer(server.id)
      setNotice(`DEFAULT ROUTE -> ${server.name}`)
      await loadBase()
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function toggleServer(server:SavedServer) {
    if(!superadmin)return
    setBusy(`server-toggle-${server.id}`);setError('')
    try {
      await api(`/api/plex/servers/${server.id}`,{method:'PATCH',body:JSON.stringify({enabled:!server.enabled})})
      await loadBase()
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function discoverLibraries(server:SavedServer) {
    setBusy(`discover-${server.id}`);setError('')
    try {
      const rows=await api<LibraryRow[]>(`/api/plex/servers/${server.id}/libraries/discover`,{method:'POST'})
      setNotice(`LIBRARY MAP REFRESHED // ${rows.length} found on ${server.name}`)
      await loadBase()
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function patchLibrary(lib:LibraryRow,patch:Partial<Pick<LibraryRow,'enabled'|'visible_to_members'>>) {
    setBusy(`lib-${lib.id}`);setError('')
    try {
      await api(`/api/plex/libraries/${lib.id}`,{method:'PATCH',body:JSON.stringify(patch)})
      await loadBase()
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function scanLibrary(lib:LibraryRow) {
    setBusy(`scan-lib-${lib.id}`);setError('')
    try { await api(`/api/plex/libraries/${lib.id}/scan`,{method:'POST'});setNotice(`SCAN QUEUED // ${lib.title}`);await loadBase() }
    catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function scanServer(server:SavedServer) {
    setBusy(`scan-server-${server.id}`);setError('')
    try { const r=await api<any>(`/api/plex/servers/${server.id}/scan`,{method:'POST'});setNotice(`SERVER SCAN QUEUED // ${r.queued} libraries`);await loadBase() }
    catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function scanAll() {
    setBusy('scan-all');setError('')
    try { const r=await api<any>('/api/plex/scans/full',{method:'POST'});setNotice(`GLOBAL SCAN QUEUED // ${r.queued} libraries`);await loadBase() }
    catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  async function saveManual(e:React.FormEvent) {
    e.preventDefault(); if(!superadmin)return
    setBusy('manual');setError('')
    try {
      const r=await api<SavedServer>('/api/plex/settings',{method:'PUT',body:JSON.stringify({base_url:manual.base_url,token:manual.token||null,set_active:true})})
      setManual({base_url:'',token:''});setNotice(`MANUAL NODE CONNECTED // ${r.name}`);await loadBase()
    } catch(e:any){setError(e.message)} finally{setBusy('')}
  }

  return <div className="page hacker-page">
    <div className="command-hero">
      <div>
        <div className="terminal-kicker"><Terminal size={14}/> /OPS/PLEX_NETWORK</div>
        <h1>PLEX NODE CONTROL</h1>
        <p>Discover account servers, probe every advertised route, map libraries per node and dispatch scans without locking Plumbus to one Plex host.</p>
      </div>
      <div className="hero-actions">
        {canAdmin&&<button className="btn primary" disabled={busy==='scan-all'} onClick={scanAll}>{busy==='scan-all'?<Loader2 className="spin" size={15}/>:<ScanLine size={15}/>}SCAN ALL ENABLED</button>}
        <button className="btn" onClick={()=>void loadBase()}><RefreshCw size={15}/>REFRESH STATE</button>
      </div>
    </div>

    {error&&<div className="terminal-alert bad"><ShieldAlert size={17}/><pre>{error}</pre></div>}
    {notice&&<div className="terminal-alert good"><CheckCircle2 size={17}/><pre>{notice}</pre></div>}

    <div className="ops-grid">
      <section className="terminal-panel span-2">
        <div className="terminal-panel-head"><div><span>01</span> SAVED PLEX NODES</div><div className="live-dot">{servers.length} KNOWN</div></div>
        <div className="node-grid">
          {servers.length===0&&<div className="empty-terminal">NO SAVED SERVERS // link Plex below or add a manual route.</div>}
          {servers.map(server=><button key={server.id} className={`node-card ${selected?.id===server.id?'selected':''} ${!server.enabled?'offline':''}`} onClick={()=>setSelectedServer(server.id)}>
            <div className="node-top"><Server size={18}/><span className={`status-led ${server.enabled?'good':'bad'}`}/></div>
            <strong>{server.name}</strong>
            <code>{server.base_url}</code>
            <div className="node-meta"><span>{server.active?'DEFAULT':'STANDBY'}</span><span>{server.enabled_library_count}/{server.library_count} LIBS</span></div>
          </button>)}
        </div>
        {selected&&<div className="node-console">
          <div className="console-line"><span>NODE_ID</span><code>{selected.id}</code></div>
          <div className="console-line"><span>MACHINE</span><code>{selected.identifier||'UNKNOWN'}</code></div>
          <div className="console-line"><span>VERSION</span><code>{selected.version||'UNKNOWN'}</code></div>
          <div className="console-line"><span>ROUTE</span><code>{selected.base_url}</code></div>
          <div className="console-actions">
            {canAdmin&&<button className="btn" disabled={busy===`test-${selected.id}`} onClick={()=>testServer(selected)}><Activity size={14}/>TEST NODE</button>}
            {canAdmin&&!selected.active&&<button className="btn" disabled={busy===`activate-${selected.id}`} onClick={()=>activateServer(selected)}><Power size={14}/>MAKE DEFAULT</button>}
            {canAdmin&&<button className="btn" disabled={busy===`discover-${selected.id}`} onClick={()=>discoverLibraries(selected)}><Database size={14}/>REFRESH LIBRARIES</button>}
            {canAdmin&&<button className="btn primary" disabled={busy===`scan-server-${selected.id}`} onClick={()=>scanServer(selected)}><Play size={14}/>SCAN THIS SERVER</button>}
            {superadmin&&<button className={`btn ${selected.enabled?'danger':''}`} disabled={busy===`server-toggle-${selected.id}`} onClick={()=>toggleServer(selected)}>{selected.enabled?<Unplug size={14}/>:<Cable size={14}/>} {selected.enabled?'DISABLE':'ENABLE'}</button>}
          </div>
        </div>}
      </section>

      <section className="terminal-panel">
        <div className="terminal-panel-head"><div><span>02</span> PLEX ACCOUNT LINK</div><div className={`live-dot ${accountLinked?'ok':'warn'}`}>{accountLinked?'LINKED':'UNLINKED'}</div></div>
        {!superadmin?<div className="empty-terminal">SUPERADMIN ACCESS REQUIRED FOR ACCOUNT LINKING.</div>:<div className="terminal-body">
          <p className="mono-copy">Use Plex authentication to discover every Plex Media Server available to this account. Tokens stay encrypted server-side.</p>
          <div className="stack-actions"><button className="btn primary" disabled={busy==='plex-signin'} onClick={signInPlex}><Link2 size={14}/>{accountLinked?'RE-LINK PLEX ACCOUNT':'SIGN IN TO PLEX'}</button><button className="btn" disabled={!accountLinked||busy==='account-servers'} onClick={refreshAccountServers}><Globe2 size={14}/>DISCOVER ACCOUNT NODES</button></div>
        </div>}
      </section>

      <section className="terminal-panel">
        <div className="terminal-panel-head"><div><span>03</span> MANUAL ROUTE</div><div className="live-dot">FALLBACK</div></div>
        {!superadmin?<div className="empty-terminal">SUPERADMIN ACCESS REQUIRED.</div>:<form className="terminal-body" onSubmit={saveManual}>
          <label className="label">PLEX BASE URL</label><input className="input" required placeholder="http://192.168.1.50:32400" value={manual.base_url} onChange={e=>setManual({...manual,base_url:e.target.value})}/>
          <label className="label">SERVER ACCESS TOKEN</label><input className="input" type="password" placeholder="Leave blank only when updating an existing token-backed route" value={manual.token} onChange={e=>setManual({...manual,token:e.target.value})}/>
          <button className="btn" disabled={busy==='manual'}><Wifi size={14}/>TEST + SAVE ROUTE</button>
        </form>}
      </section>
    </div>

    {superadmin&&accountServers.length>0&&<section className="terminal-panel section-space">
      <div className="terminal-panel-head"><div><span>04</span> DISCOVERED ACCOUNT NODES</div><div className="live-dot ok">LIVE PROBES</div></div>
      <div className="account-node-list">{accountServers.map(node=><div className="account-node" key={node.client_identifier}>
        <div className="account-node-title"><div><Server size={17}/><strong>{node.name}</strong>{node.owned&&<span className="micro-badge">OWNED</span>}</div><code>{node.client_identifier}</code></div>
        <div className="route-list">{node.connections.map(route=><div className={`route-row ${route.reachable?'reachable':'unreachable'}`} key={route.uri}>
          <div className="route-icon">{route.reachable?<Wifi size={15}/>:<WifiOff size={15}/>}</div>
          <div className="route-info"><code>{route.uri}</code><small>{route.local?'LOCAL':'REMOTE'} // {route.relay?'RELAY':'DIRECT'} // {route.protocol||'?'}</small>{route.error&&<small className="error-line">{route.error}</small>}</div>
          <button className="btn" disabled={busy===`connect-${node.client_identifier}-${route.uri}`} onClick={()=>connectAccountServer(node,route.uri)}>TRY ROUTE</button>
        </div>)}</div>
        <button className="btn primary" disabled={busy===`connect-${node.client_identifier}-auto`} onClick={()=>connectAccountServer(node)}>AUTO CONNECT BEST ROUTE</button>
      </div>)}</div>
    </section>}

    <section className="terminal-panel section-space">
      <div className="terminal-panel-head"><div><span>05</span> LIBRARY MAP {selected?`// ${selected.name.toUpperCase()}`:''}</div><div className="live-dot">{visibleLibraries.length} FOUND</div></div>
      <div className="table-wrap"><table className="terminal-table"><thead><tr><th>Library</th><th>Type</th><th>Index</th><th>Member Visibility</th><th>Last Scan</th><th>Action</th></tr></thead><tbody>{visibleLibraries.map(lib=><tr key={lib.id}>
        <td><strong>{lib.title}</strong><div className="tiny-code">KEY:{lib.plex_key}</div></td><td>{lib.type}</td><td><button disabled={!canAdmin||busy===`lib-${lib.id}`} className={`switch-chip ${lib.enabled?'on':'off'}`} onClick={()=>patchLibrary(lib,{enabled:!lib.enabled})}>{lib.enabled?'ENABLED':'DISABLED'}</button></td><td><button disabled={!canAdmin||busy===`lib-${lib.id}`} className={`switch-chip ${lib.visible_to_members?'on':'off'}`} onClick={()=>patchLibrary(lib,{visible_to_members:!lib.visible_to_members})}>{lib.visible_to_members?'VISIBLE':'STAFF ONLY'}</button></td><td>{lib.last_scan_at?new Date(lib.last_scan_at).toLocaleString():'NEVER'}</td><td><button className="btn" disabled={!canAdmin||!lib.enabled||busy===`scan-lib-${lib.id}`} onClick={()=>scanLibrary(lib)}><ScanLine size={14}/>SCAN</button></td>
      </tr>)}</tbody></table></div>
      {visibleLibraries.length===0&&<div className="empty-terminal">NO LIBRARIES MAPPED FOR THIS NODE // run REFRESH LIBRARIES.</div>}
    </section>

    <section className="terminal-panel section-space">
      <div className="terminal-panel-head"><div><span>06</span> SCAN EXECUTION LOG</div><div className="live-dot">LAST {selectedScans.length}</div></div>
      <div className="table-wrap"><table className="terminal-table"><thead><tr><th>Job</th><th>Node / Library</th><th>Mode</th><th>Status</th><th>Scanned</th><th>Δ</th><th>Error / Finished</th></tr></thead><tbody>{selectedScans.map(scan=><tr key={scan.id}>
        <td>#{scan.id}</td><td>{scan.server_name||`SERVER ${scan.server_id??'?'}`}<div className="tiny-code">{scan.library_name||`LIB ${scan.library_id??'?'}`}</div></td><td>{scan.mode}</td><td><span className={`micro-badge ${scan.status==='completed'?'good':scan.status==='failed'?'bad':'warn'}`}>{scan.status.toUpperCase()}</span></td><td>{scan.items_scanned}</td><td>+{scan.items_added} ~{scan.items_updated} -{scan.items_removed}</td><td>{scan.last_error?<span className="error-line">{scan.last_error}</span>:scan.finished_at?new Date(scan.finished_at).toLocaleString():'RUNNING / QUEUED'}</td>
      </tr>)}</tbody></table></div>
    </section>
  </div>
}
