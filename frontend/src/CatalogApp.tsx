import {
  ChevronRight,
  Film,
  Gauge,
  Library,
  ListVideo,
  LogOut,
  Play,
  Search,
  Shield,
  Sparkles,
  Terminal,
  Ticket,
  Tv,
  Users,
} from 'lucide-react'
import { ReactNode, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import './catalog.css'

type User={id:number;username:string;global_name?:string;role:'SuperAdmin'|'Admin'|'Support'|'Member';status:string}
type MediaVersion={video_codec?:string;audio_codec?:string}
type MediaItem={
  id:number
  media_type:'movie'|'show'|'season'|'episode'
  title:string
  year?:number
  poster_url?:string
  backdrop_url?:string
  genres?:string[]
  qualities?:string[]
  collections?:string[]
  summary?:string
  duration_ms?:number
  rating?:string
  playable?:boolean
  is_anime?:boolean
  season_number?:number
  episode_number?:number
  parent_title?:string
  grandparent_title?:string
  season_count?:number
  episode_count?:number
  seasons?:MediaItem[]
  media?:MediaVersion[]
  library?:{id:number;title:string;server_name?:string}
}
type Collection={name:string;count:number;movie_count:number;show_count:number;representative_media_id:number;representative_title:string;poster_url?:string}
type BrowseMode='all'|'movie'|'show'|'anime'
type CacheEntry={expires:number;value:unknown}

const responseCache=new Map<string,CacheEntry>()

async function api<T>(path:string,init?:RequestInit):Promise<T>{
  const response=await fetch(path,{credentials:'include',headers:{'Content-Type':'application/json',...(init?.headers||{})},...init})
  if(!response.ok){
    const body=await response.json().catch(()=>({detail:`HTTP ${response.status}`}))
    throw new Error(typeof body?.detail==='string'?body.detail:JSON.stringify(body?.detail||body))
  }
  return response.json()
}

function peekCached<T>(path:string):T|undefined{
  const entry=responseCache.get(path)
  if(!entry)return undefined
  if(entry.expires<=Date.now()){responseCache.delete(path);return undefined}
  return entry.value as T
}

async function cachedApi<T>(path:string,ttlMs=60000):Promise<T>{
  const cached=peekCached<T>(path)
  if(cached!==undefined)return cached
  const value=await api<T>(path)
  responseCache.set(path,{value,expires:Date.now()+ttlMs})
  return value
}

function prefetchMedia(id:number){
  void cachedApi<MediaItem>(`/api/movies/${id}`,120000).catch(()=>{})
}

function isDirectReady(item:MediaItem):boolean{
  const videoReady=new Set(['h264','avc','avc1'])
  const audioReady=new Set(['aac','aac-lc','aac_lc','mp4a'])
  return Boolean(item.media?.some(version=>
    videoReady.has(String(version.video_codec||'').toLowerCase())&&
    audioReady.has(String(version.audio_codec||'').toLowerCase()),
  ))
}

function Shell({user,children}:{user:User;children:ReactNode}){
  const navigate=useNavigate()
  const admin=['SuperAdmin','Admin','Support'].includes(user.role)
  async function logout(){responseCache.clear();await api('/api/auth/logout',{method:'POST'});navigate('/login')}
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand">PLUMBUS<span> // STREAM</span></div>
      <div className="sidebar-status"><span className="status-led good"/><code>CATALOG ONLINE</code></div>
      <div className="nav-group"><div className="nav-label">/cinema</div>
        <Link className={`nav-link ${location.pathname==='/browse'?'active':''}`} to="/browse"><Film size={17}/>BROWSE</Link>
        <Link className={`nav-link ${location.pathname==='/search'?'active':''}`} to="/search"><Search size={17}/>SEARCH</Link>
        <Link className={`nav-link ${location.pathname==='/collections'?'active':''}`} to="/collections"><Library size={17}/>COLLECTIONS</Link>
      </div>
      {admin&&<div className="nav-group"><div className="nav-label">/operations</div>
        <a className="nav-link" href="/admin"><Gauge size={17}/>DASHBOARD</a>
        {user.role!=='Support'&&<><a className="nav-link" href="/admin/users"><Users size={17}/>USERS</a><a className="nav-link" href="/admin/invites"><Ticket size={17}/>INVITES</a></>}
        <a className="nav-link" href="/admin/plex"><ListVideo size={17}/>PLEX NODES</a>
        <a className="nav-link" href="/admin/logs"><Shield size={17}/>AUDIT LOG</a>
      </div>}
    </aside>
    <main className="main"><header className="topbar"><div className="topbar-user"><span className="status-led good"/><strong>{user.global_name||user.username}</strong><span className="micro-badge">{user.role.toUpperCase()}</span></div><div className="topbar-path"><code>PLUMBUS://CATALOG</code></div><button className="btn" onClick={logout}><LogOut size={15}/>SIGN OUT</button></header>{children}</main>
  </div>
}

function MediaBadge({item}:{item:MediaItem}){
  const label=item.is_anime?'ANIME':item.media_type==='show'?'SERIES':'MOVIE'
  return <span className={`catalog-badge ${item.is_anime?'anime':''}`}>{label}</span>
}

function PosterCard({item,index=0}:{item:MediaItem;index?:number}){
  return <Link className="catalog-poster-card" to={`/media/${item.id}`} onPointerEnter={()=>prefetchMedia(item.id)} onFocus={()=>prefetchMedia(item.id)}>
    <div className="catalog-poster">
      {item.poster_url?<img src={item.poster_url} alt={item.title} loading={index<8?'eager':'lazy'} decoding="async"/>:<div className="catalog-poster-empty"><Film size={32}/></div>}
      <MediaBadge item={item}/>
      <div className="catalog-hover-play"><Play fill="currentColor" size={28}/></div>
    </div>
    <div className="poster-title crt-title" data-text={item.title}>{item.title}</div>
    <div className="catalog-meta"><span>{item.year||'—'}</span>{item.media_type==='show'&&<span>{item.episode_count||0} EPISODES</span>}{item.qualities?.[0]&&item.media_type==='movie'&&<span>{item.qualities[0]}</span>}</div>
  </Link>
}

function PosterGrid({items}:{items:MediaItem[]}){
  return <div className="catalog-grid">{items.map((item,index)=><PosterCard key={item.id} item={item} index={index}/>)}</div>
}

function CatalogPage({searchOnly=false}:{searchOnly?:boolean}){
  const [items,setItems]=useState<MediaItem[]>([])
  const [q,setQ]=useState('')
  const [mode,setMode]=useState<BrowseMode>('all')
  const [loading,setLoading]=useState(!searchOnly)
  const [error,setError]=useState('')

  useEffect(()=>{
    if(searchOnly&&!q.trim()){setItems([]);setLoading(false);return}
    let cancelled=false
    const timer=window.setTimeout(async()=>{
      setError('')
      const params=new URLSearchParams({limit:'36'})
      if(q.trim())params.set('q',q.trim())
      if(mode==='movie'||mode==='show')params.set('media_type',mode)
      if(mode==='anime')params.set('anime','true')
      const path=`/api/movies?${params}`
      const cached=peekCached<{items:MediaItem[]}>(path)
      if(cached){setItems(cached.items);setLoading(false)}else setLoading(true)
      try{
        const response=await cachedApi<{items:MediaItem[]}>(path,45000)
        if(!cancelled)setItems(response.items)
      }catch(e:any){if(!cancelled)setError(e.message)}finally{if(!cancelled)setLoading(false)}
    },q?250:0)
    return()=>{cancelled=true;window.clearTimeout(timer)}
  },[q,mode,searchOnly])

  const filters:[BrowseMode,string,ReactNode][]=[['all','ALL',<Film size={14}/>],['movie','MOVIES',<Film size={14}/>],['show','TV / SERIES',<Tv size={14}/>],['anime','ANIME',<Sparkles size={14}/>]]
  return <div className="page catalog-page">
    <section className={`catalog-hero ${searchOnly?'search-mode':''}`}>
      <div className="terminal-kicker"><Terminal size={14}/>{searchOnly?' /TITLE_SEARCH':' /STREAM_LIBRARY'}</div>
      <h1>{searchOnly?'FIND A TITLE':'WHAT DO YOU WANT TO WATCH?'}</h1>
      <p>{searchOnly?'Title matches are ranked first. Cast, genre and collection matches are secondary.':'Fast access to movies, series and anime across every enabled Plex node.'}</p>
      <div className="catalog-search"><Search size={20}/><input autoFocus={searchOnly} value={q} onChange={e=>setQ(e.target.value)} placeholder={searchOnly?'Search titles…':'Search titles…'}/>{q&&<button onClick={()=>setQ('')}>CLEAR</button>}</div>
      <div className="catalog-filters">{filters.map(([value,label,icon])=><button key={value} className={mode===value?'active':''} onClick={()=>setMode(value)}>{icon}{label}</button>)}</div>
    </section>
    <div className="catalog-heading"><div><h2>{searchOnly?(q?`RESULTS FOR “${q}”`:'SEARCH BY TITLE'):'RECENTLY ADDED'}</h2><span>{items.length} TITLES</span></div></div>
    {error&&<div className="terminal-alert bad">{error}</div>}
    {loading&&!items.length?<div className="catalog-grid">{Array.from({length:18}).map((_,index)=><div className="catalog-poster skeleton" key={index}/>)}</div>:items.length?<PosterGrid items={items}/>:<div className="catalog-empty">{searchOnly&&!q?'START TYPING A MOVIE, SHOW OR ANIME TITLE':'NO MATCHING TITLES'}</div>}
  </div>
}

function CollectionsPage(){
  const [collections,setCollections]=useState<Collection[]>(()=>peekCached<{collections:Collection[]}>('/api/movies/collections/list')?.collections||[])
  const [selected,setSelected]=useState<string|null>(null)
  const [items,setItems]=useState<MediaItem[]>([])
  const [loading,setLoading]=useState(!collections.length)
  const [error,setError]=useState('')
  useEffect(()=>{cachedApi<{collections:Collection[]}>('/api/movies/collections/list',120000).then(r=>setCollections(r.collections)).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[])
  useEffect(()=>{if(!selected){setItems([]);return}const path=`/api/movies?collection=${encodeURIComponent(selected)}&sort=alphabetical&limit=120`;const cached=peekCached<{items:MediaItem[]}>(path);if(cached){setItems(cached.items);setLoading(false)}else setLoading(true);cachedApi<{items:MediaItem[]}>(path,60000).then(r=>setItems(r.items)).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[selected])
  if(selected)return <div className="page catalog-page"><button className="collection-back" onClick={()=>setSelected(null)}>← ALL COLLECTIONS</button><div className="catalog-heading"><div><h1>{selected}</h1><span>{items.length} TITLES</span></div></div>{loading&&!items.length?<div className="catalog-grid">{Array.from({length:12}).map((_,i)=><div className="catalog-poster skeleton" key={i}/>)}</div>:<PosterGrid items={items}/>}</div>
  return <div className="page catalog-page"><section className="catalog-hero collections-hero"><div className="terminal-kicker"><Library size={14}/> /PLEX_COLLECTIONS</div><h1>COLLECTIONS</h1><p>Curated groups imported directly from Plex metadata.</p></section>{error&&<div className="terminal-alert bad">{error}</div>}{loading&&!collections.length?<div className="collection-grid">{Array.from({length:8}).map((_,i)=><div className="collection-card skeleton" key={i}/>)}</div>:collections.length?<div className="collection-grid">{collections.map(collection=><button key={collection.name} className="collection-card" onClick={()=>setSelected(collection.name)}>{collection.poster_url?<img src={collection.poster_url} alt="" loading="lazy" decoding="async"/>:<div className="collection-art-empty"><Library/></div>}<div className="collection-shade"/><div className="collection-copy"><strong>{collection.name}</strong><span>{collection.count} TITLES · {collection.movie_count} MOVIES · {collection.show_count} SERIES</span></div><ChevronRight/></button>)}</div>:<div className="catalog-empty">NO PLEX COLLECTION TAGS HAVE BEEN INDEXED YET</div>}</div>
}

function formatRuntime(ms?:number){if(!ms)return null;const minutes=Math.round(ms/60000);return minutes>=60?`${Math.floor(minutes/60)}h ${minutes%60}m`:`${minutes}m`}

function EpisodeCard({episode}:{episode:MediaItem}){
  return <div className="episode-card">
    <Link to={`/media/${episode.id}`} className="episode-thumb" onPointerEnter={()=>prefetchMedia(episode.id)}>{episode.poster_url?<img src={episode.poster_url} alt="" loading="lazy" decoding="async"/>:<div className="episode-thumb-empty"><Tv/></div>}<span className="episode-number">E{episode.episode_number??'?'}</span></Link>
    <div className="episode-card-copy"><div><strong>{episode.title}</strong><span>{formatRuntime(episode.duration_ms)}{episode.qualities?.[0]?` · ${episode.qualities[0]}`:''}</span></div><p>{episode.summary||'No episode description supplied by Plex.'}</p><a className="episode-play" href={`/watch/${episode.id}`}><Play fill="currentColor" size={16}/> PLAY</a></div>
  </div>
}

function MediaDetailPage(){
  const {id}=useParams()
  const detailPath=`/api/movies/${id}`
  const cachedDetail=peekCached<MediaItem>(detailPath)
  const [item,setItem]=useState<MediaItem|null>(cachedDetail||null)
  const [episodes,setEpisodes]=useState<MediaItem[]>([])
  const [season,setSeason]=useState<number|null>(cachedDetail?.media_type==='show'&&cachedDetail.seasons?.length?cachedDetail.seasons[0].season_number??0:null)
  const [loading,setLoading]=useState(!cachedDetail)
  const [episodesLoading,setEpisodesLoading]=useState(false)
  const [error,setError]=useState('')

  useEffect(()=>{const path=`/api/movies/${id}`;const cached=peekCached<MediaItem>(path);if(cached){setItem(cached);setLoading(false);if(cached.media_type==='show'&&cached.seasons?.length)setSeason(current=>current??cached.seasons![0].season_number??0)}else{setLoading(true);setItem(null)}setError('');cachedApi<MediaItem>(path,120000).then(media=>{setItem(media);if(media.media_type==='show'&&media.seasons?.length)setSeason(current=>current??media.seasons![0].season_number??0)}).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[id])
  useEffect(()=>{if(!item||item.media_type!=='show'||season===null)return;const path=`/api/movies/${item.id}/episodes?season_number=${season}`;const cached=peekCached<{episodes:MediaItem[]}>(path);if(cached){setEpisodes(cached.episodes);setEpisodesLoading(false)}else setEpisodesLoading(true);let cancelled=false;cachedApi<{episodes:MediaItem[]}>(path,60000).then(r=>{if(!cancelled)setEpisodes(r.episodes)}).catch(e=>{if(!cancelled)setError(e.message)}).finally(()=>{if(!cancelled)setEpisodesLoading(false)});return()=>{cancelled=true}},[item?.id,item?.media_type,season])

  if(loading&&!item)return <div className="page"><div className="skeleton" style={{height:'70vh'}}/></div>
  if(error&&!item)return <div className="page"><div className="terminal-alert bad">{error}</div></div>
  if(!item)return null
  const playable=item.playable&&(item.media_type==='movie'||item.media_type==='episode')
  const ready=isDirectReady(item)
  return <div className={`stream-detail ${item.is_anime?'anime-detail':''}`}>
    {item.backdrop_url&&<img className="stream-backdrop" src={item.backdrop_url} alt="" decoding="async"/>}<div className="stream-detail-shade"/>
    <div className="stream-detail-content"><div className="terminal-kicker"><Terminal size={14}/> {item.is_anime?'ANIME':'MEDIA'} // {item.library?.server_name||'PLEX'}</div><div className="stream-pills"><span>{item.is_anime?'ANIME':item.media_type.toUpperCase()}</span>{item.year&&<span>{item.year}</span>}{item.rating&&<span>{item.rating}</span>}{item.qualities?.map(q=><span key={q}>{q}</span>)}{ready&&<span title="H.264/AVC video with AAC-LC compatible audio">READY</span>}</div><h1>{item.title}</h1><p>{item.summary}</p><div className="stream-meta">{formatRuntime(item.duration_ms)}{item.genres?.length?` · ${item.genres.slice(0,4).join(' · ')}`:''}</div>{playable&&<div className="stream-actions"><a className="netflix-play" href={`/watch/${item.id}`}><Play fill="currentColor" size={24}/> PLAY</a></div>}</div>
    {item.media_type==='show'&&<section className="series-browser"><div className="series-browser-head"><div><h2>EPISODES</h2><span>{item.season_count||0} SEASONS · {item.episode_count||0} EPISODES</span></div><select value={season??0} onChange={e=>setSeason(Number(e.target.value))}>{item.seasons?.map(s=><option key={s.id} value={s.season_number??0}>{s.season_number===0?'Specials':`Season ${s.season_number}`} · {s.episode_count||0} episodes</option>)}</select></div>{episodesLoading&&!episodes.length?<div className="episode-grid">{Array.from({length:6}).map((_,i)=><div className="episode-card skeleton" key={i}/>)}</div>:<div className="episode-grid">{episodes.map(ep=><EpisodeCard episode={ep} key={ep.id}/>)}</div>}</section>}
    {error&&<div className="terminal-alert bad detail-error">{error}</div>}
  </div>
}

export function CatalogApp(){
  const location=useLocation()
  const [user,setUser]=useState<User|null|undefined>(undefined)
  useEffect(()=>{api<User>('/api/auth/me').then(setUser).catch(()=>setUser(null))},[])
  const content=useMemo(()=>{
    if(location.pathname==='/search')return <CatalogPage searchOnly/>
    if(location.pathname==='/collections')return <CollectionsPage/>
    if(/^\/(media|movie)\/\d+$/.test(location.pathname))return <MediaDetailPage/>
    return <CatalogPage/>
  },[location.pathname])
  if(user===undefined)return <div className="catalog-boot"><div className="watch-spinner"/>LOADING CATALOG…</div>
  if(user===null){window.location.href='/login';return null}
  return <Shell user={user}>{content}</Shell>
}
