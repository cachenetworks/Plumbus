import {
  CheckCircle2,
  ChevronRight,
  Eye,
  Film,
  Gauge,
  History,
  Library,
  ListVideo,
  LogOut,
  Play,
  RotateCcw,
  Search,
  Shield,
  Sparkles,
  Tags,
  Terminal,
  Ticket,
  Tv,
  Users,
} from 'lucide-react'
import { ReactNode, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import { VrChatButton } from './PlaybackToolbar'
import './catalog.css'
import './catalog-status.css'

type User={id:number;username:string;global_name?:string;role:'SuperAdmin'|'Admin'|'Support'|'Member';status:string}
type MediaVersion={video_codec?:string;audio_codec?:string}
type HistoryState={position_ms:number;completed:boolean;last_watched_at?:string|null}
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
  direct_ready?:boolean
  season_number?:number
  episode_number?:number
  parent_title?:string
  grandparent_title?:string
  season_count?:number
  episode_count?:number
  seasons?:MediaItem[]
  media?:MediaVersion[]
  library?:{id:number;title:string;server_name?:string}
  history?:HistoryState
  position_ms?:number
  completed?:boolean
  last_watched_at?:string|null
}
type Collection={name:string;count:number;movie_count:number;show_count:number;representative_media_id:number;representative_title:string;poster_url?:string}
type Category={name:string;count:number}
type BrowseMode='all'|'movie'|'show'|'anime'
type CacheEntry={expires:number;value:unknown}
type CatalogStatus={ready:boolean;qualities?:string[];ready_count?:number;episode_count?:number}
type CatalogStatusResponse={items:Record<string,CatalogStatus>}
type HistoryStatusResponse={items:Record<string,HistoryState>}

const responseCache=new Map<string,CacheEntry>()

async function api<T>(path:string,init?:RequestInit):Promise<T>{
  const response=await fetch(path,{credentials:'include',headers:{'Content-Type':'application/json',...(init?.headers||{})},...init})
  if(!response.ok){
    const body=await response.json().catch(()=>({detail:`HTTP ${response.status}`}))
    throw new Error(typeof body?.detail==='string'?body.detail:JSON.stringify(body?.detail||body))
  }
  if(response.status===204)return undefined as T
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

function clearHistoryCache(){
  for(const key of [...responseCache.keys()])if(key.startsWith('/api/history'))responseCache.delete(key)
}

function statusPath(items:MediaItem[]):string{return `/api/catalog/status?ids=${items.map(item=>item.id).join(',')}`}
function historyStatusPath(items:MediaItem[]):string{return `/api/history/status?ids=${items.map(item=>item.id).join(',')}`}

async function enrichCatalogStatus(items:MediaItem[]):Promise<MediaItem[]>{
  if(!items.length)return items
  const statuses=await cachedApi<CatalogStatusResponse>(statusPath(items),30000)
  return items.map(item=>{
    const status=statuses.items[String(item.id)]
    return status?{...item,direct_ready:status.ready,qualities:status.qualities?.length?status.qualities:item.qualities}:item
  })
}

async function enrichHistory(items:MediaItem[]):Promise<MediaItem[]>{
  if(!items.length)return items
  const statuses=await cachedApi<HistoryStatusResponse>(historyStatusPath(items),15000)
  return items.map(item=>({...item,history:statuses.items[String(item.id)]||{position_ms:0,completed:false,last_watched_at:null}}))
}

async function enrichItems(items:MediaItem[]):Promise<MediaItem[]>{
  const withCatalog=await enrichCatalogStatus(items).catch(()=>items)
  return enrichHistory(withCatalog).catch(()=>withCatalog)
}

function prefetchMedia(id:number){
  void cachedApi<MediaItem>(`/api/movies/${id}`,120000).catch(()=>{})
  void cachedApi<CatalogStatusResponse>(`/api/catalog/status?ids=${id}`,30000).catch(()=>{})
  void cachedApi<HistoryStatusResponse>(`/api/history/status?ids=${id}`,15000).catch(()=>{})
}

function isDirectReady(item:MediaItem):boolean{
  if(item.direct_ready!==undefined)return item.direct_ready
  const videoReady=new Set(['h264','avc','avc1'])
  const audioReady=new Set(['aac','aac-lc','aac_lc','mp4a'])
  return Boolean(item.media?.some(version=>videoReady.has(String(version.video_codec||'').toLowerCase())&&audioReady.has(String(version.audio_codec||'').toLowerCase())))
}

function qualityRank(value:string):number{
  const normalized=value.toLowerCase()
  if(normalized.includes('8k')||normalized.includes('4320'))return 8000
  if(normalized.includes('4k')||normalized.includes('2160'))return 4000
  if(normalized.includes('1440'))return 1440
  if(normalized.includes('1080'))return 1080
  if(normalized.includes('720'))return 720
  if(normalized.includes('576'))return 576
  if(normalized.includes('480'))return 480
  return 0
}

function bestQuality(values?:string[]):string|undefined{return values?.filter(Boolean).sort((a,b)=>qualityRank(b)-qualityRank(a))[0]}
function progressPercent(item:MediaItem):number{
  const position=item.history?.position_ms??item.position_ms??0
  if(!position||!item.duration_ms)return 0
  return Math.max(0,Math.min(100,(position/item.duration_ms)*100))
}

function Shell({user,children}:{user:User;children:ReactNode}){
  const navigate=useNavigate();const location=useLocation();const admin=['SuperAdmin','Admin','Support'].includes(user.role)
  async function logout(){responseCache.clear();await api('/api/auth/logout',{method:'POST'});navigate('/login')}
  return <div className="shell"><aside className="sidebar"><div className="brand">PLUMBUS<span> // STREAM</span></div><div className="sidebar-status"><span className="status-led good"/><code>CATALOG ONLINE</code></div><div className="nav-group"><div className="nav-label">/cinema</div>
    <Link className={`nav-link ${location.pathname==='/browse'?'active':''}`} to="/browse"><Film size={17}/>BROWSE</Link>
    <Link className={`nav-link ${location.pathname==='/search'?'active':''}`} to="/search"><Search size={17}/>SEARCH</Link>
    <Link className={`nav-link ${location.pathname==='/categories'?'active':''}`} to="/categories"><Tags size={17}/>CATEGORIES</Link>
    <Link className={`nav-link ${location.pathname==='/collections'?'active':''}`} to="/collections"><Library size={17}/>COLLECTIONS</Link>
    <Link className={`nav-link ${location.pathname==='/watched'?'active':''}`} to="/watched"><History size={17}/>WATCHED</Link>
  </div>{admin&&<div className="nav-group"><div className="nav-label">/operations</div><a className="nav-link" href="/admin"><Gauge size={17}/>DASHBOARD</a>{user.role!=='Support'&&<><a className="nav-link" href="/admin/users"><Users size={17}/>USERS</a><a className="nav-link" href="/admin/invites"><Ticket size={17}/>INVITES</a></>}<a className="nav-link" href="/admin/plex"><ListVideo size={17}/>PLEX NODES</a><a className="nav-link" href="/admin/logs"><Shield size={17}/>AUDIT LOG</a></div>}</aside>
    <main className="main"><header className="topbar"><div className="topbar-user"><span className="status-led good"/><strong>{user.global_name||user.username}</strong><span className="micro-badge">{user.role.toUpperCase()}</span></div><div className="topbar-path"><code>PLUMBUS://CATALOG</code></div><button className="btn" onClick={logout}><LogOut size={15}/>SIGN OUT</button></header>{children}</main></div>
}

function MediaBadge({item}:{item:MediaItem}){return <span className={`catalog-badge ${item.is_anime?'anime':''}`}>{item.is_anime?'ANIME':item.media_type==='show'?'SERIES':'MOVIE'}</span>}

function PosterCard({item,index=0}:{item:MediaItem;index?:number}){
  const quality=bestQuality(item.qualities);const watched=item.history?.completed??item.completed??false;const progress=progressPercent(item)
  return <Link className="catalog-poster-card" to={`/media/${item.id}`} onPointerEnter={()=>prefetchMedia(item.id)} onFocus={()=>prefetchMedia(item.id)}><div className="catalog-poster">{item.poster_url?<img src={item.poster_url} alt={item.title} loading={index<8?'eager':'lazy'} decoding="async"/>:<div className="catalog-poster-empty"><Film size={32}/></div>}<MediaBadge item={item}/>{watched&&<span className="watched-overlay"><CheckCircle2 size={13}/>WATCHED</span>}<div className="catalog-hover-play"><Play fill="currentColor" size={28}/></div>{progress>0&&!watched&&<div className="watch-progress"><span style={{width:`${progress}%`}}/></div>}</div><div className="poster-title-line"><div className="poster-title crt-title" data-text={item.title}>{item.title}</div><div className="poster-title-status">{item.direct_ready&&<span className="poster-status-chip ready">READY</span>}{quality&&<span className="poster-status-chip quality">{quality.toUpperCase()}</span>}</div></div><div className="catalog-meta"><span>{item.year||'—'}</span>{item.media_type==='show'&&<span>{item.episode_count||0} EPISODES</span>}{progress>0&&!watched&&<span>{Math.round(progress)}% WATCHED</span>}</div></Link>
}
function PosterGrid({items}:{items:MediaItem[]}){return <div className="catalog-grid">{items.map((item,index)=><PosterCard key={item.id} item={item} index={index}/>)}</div>}

function ContinueWatching(){
  const [items,setItems]=useState<MediaItem[]>([])
  useEffect(()=>{cachedApi<MediaItem[]>('/api/history/continue-watching?limit=18',15000).then(async rows=>setItems(await enrichCatalogStatus(rows))).catch(()=>{})},[])
  if(!items.length)return null
  return <section className="catalog-section"><div className="catalog-heading"><div><h2>CONTINUE WATCHING</h2><span>{items.length} IN PROGRESS</span></div></div><PosterGrid items={items.map(item=>({...item,history:{position_ms:item.position_ms||0,completed:false,last_watched_at:item.last_watched_at}}))}/></section>
}

function CatalogPage({searchOnly=false}:{searchOnly?:boolean}){
  const [items,setItems]=useState<MediaItem[]>([]);const [q,setQ]=useState('');const [mode,setMode]=useState<BrowseMode>('all');const [genre,setGenre]=useState('');const [categories,setCategories]=useState<Category[]>([]);const [loading,setLoading]=useState(!searchOnly);const [error,setError]=useState('')
  useEffect(()=>{cachedApi<{categories:Category[]}>('/api/categories',120000).then(r=>setCategories(r.categories)).catch(()=>{})},[])
  useEffect(()=>{
    if(searchOnly&&!q.trim()){setItems([]);setLoading(false);return}
    let cancelled=false;const timer=window.setTimeout(async()=>{setError('');const params=new URLSearchParams({limit:'36'});if(q.trim())params.set('q',q.trim());if(mode==='movie'||mode==='show')params.set('media_type',mode);if(mode==='anime')params.set('anime','true');if(genre)params.set('genre',genre);const path=`/api/movies?${params}`;const cached=peekCached<{items:MediaItem[]}>(path);if(cached){setItems(cached.items);setLoading(false)}else setLoading(true);try{const response=await cachedApi<{items:MediaItem[]}>(path,45000);if(cancelled)return;setItems(await enrichItems(response.items));setLoading(false)}catch(e:any){if(!cancelled){setError(e.message);setLoading(false)}}},q?250:0);return()=>{cancelled=true;window.clearTimeout(timer)}
  },[q,mode,genre,searchOnly])
  const filters:[BrowseMode,string,ReactNode][]=[['all','ALL',<Film size={14}/>],['movie','MOVIES',<Film size={14}/>],['show','TV / SERIES',<Tv size={14}/>],['anime','ANIME',<Sparkles size={14}/>]]
  return <div className="page catalog-page"><section className={`catalog-hero ${searchOnly?'search-mode':''}`}><div className="terminal-kicker"><Terminal size={14}/>{searchOnly?' /TITLE_SEARCH':' /STREAM_LIBRARY'}</div><h1>{searchOnly?'FIND A TITLE':'WHAT DO YOU WANT TO WATCH?'}</h1><p>{searchOnly?'Title matches are ranked first. Cast, genre and collection matches are secondary.':'Movies, series and anime across every enabled Plex node.'}</p><div className="catalog-search"><Search size={20}/><input autoFocus={searchOnly} value={q} onChange={e=>setQ(e.target.value)} placeholder="Search titles…"/>{q&&<button onClick={()=>setQ('')}>CLEAR</button>}</div><div className="catalog-filters">{filters.map(([value,label,icon])=><button key={value} className={mode===value?'active':''} onClick={()=>setMode(value)}>{icon}{label}</button>)}</div>{categories.length>0&&<div className="category-strip"><button className={!genre?'active':''} onClick={()=>setGenre('')}>ALL CATEGORIES</button>{categories.slice(0,14).map(category=><button key={category.name} className={genre===category.name?'active':''} onClick={()=>setGenre(category.name)}>{category.name.toUpperCase()} <span>{category.count}</span></button>)}<Link to="/categories">MORE →</Link></div>}</section>{!searchOnly&&!q&&!genre&&mode==='all'&&<ContinueWatching/>}<div className="catalog-heading"><div><h2>{searchOnly?(q?`RESULTS FOR “${q}”`:'SEARCH BY TITLE'):genre?genre.toUpperCase():'RECENTLY ADDED'}</h2><span>{items.length} TITLES</span></div></div>{error&&<div className="terminal-alert bad">{error}</div>}{loading&&!items.length?<div className="catalog-grid">{Array.from({length:18}).map((_,index)=><div className="catalog-poster skeleton" key={index}/>)}</div>:items.length?<PosterGrid items={items}/>:<div className="catalog-empty">{searchOnly&&!q?'START TYPING A MOVIE, SHOW OR ANIME TITLE':'NO MATCHING TITLES'}</div>}</div>
}

function CategoriesPage(){
  const [categories,setCategories]=useState<Category[]>([]);const [selected,setSelected]=useState('');const [items,setItems]=useState<MediaItem[]>([]);const [loading,setLoading]=useState(true);const [error,setError]=useState('')
  useEffect(()=>{cachedApi<{categories:Category[]}>('/api/categories',120000).then(r=>setCategories(r.categories)).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[])
  useEffect(()=>{if(!selected){setItems([]);return}let cancelled=false;setLoading(true);const path=`/api/movies?genre=${encodeURIComponent(selected)}&sort=alphabetical&limit=120`;cachedApi<{items:MediaItem[]}>(path,60000).then(async r=>{if(!cancelled)setItems(await enrichItems(r.items))}).catch(e=>{if(!cancelled)setError(e.message)}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true}},[selected])
  if(selected)return <div className="page catalog-page"><button className="collection-back" onClick={()=>setSelected('')}>← ALL CATEGORIES</button><div className="catalog-heading"><div><h1>{selected}</h1><span>{items.length} TITLES</span></div></div>{loading&&!items.length?<div className="catalog-grid">{Array.from({length:12}).map((_,i)=><div className="catalog-poster skeleton" key={i}/>)}</div>:<PosterGrid items={items}/>}</div>
  return <div className="page catalog-page"><section className="catalog-hero collections-hero"><div className="terminal-kicker"><Tags size={14}/> /GENRES</div><h1>CATEGORIES</h1><p>Browse the actual genre metadata indexed from Plex.</p></section>{error&&<div className="terminal-alert bad">{error}</div>}{loading&&!categories.length?<div className="category-grid">{Array.from({length:12}).map((_,i)=><div className="category-card skeleton" key={i}/>)}</div>:<div className="category-grid">{categories.map(category=><button className="category-card" key={category.name} onClick={()=>setSelected(category.name)}><Tags size={20}/><strong>{category.name}</strong><span>{category.count} TITLES</span><ChevronRight size={18}/></button>)}</div>}</div>
}

function WatchedPage(){
  const [items,setItems]=useState<MediaItem[]>([]);const [loading,setLoading]=useState(true);const [error,setError]=useState('')
  useEffect(()=>{api<MediaItem[]>('/api/history/watched?limit=500').then(async rows=>setItems(await enrichCatalogStatus(rows))).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[])
  return <div className="page catalog-page"><section className="catalog-hero search-mode"><div className="terminal-kicker"><History size={14}/> /WATCH_HISTORY</div><h1>WATCHED</h1><p>Everything you have marked or finished as watched.</p></section>{error&&<div className="terminal-alert bad">{error}</div>}{loading&&!items.length?<div className="catalog-grid">{Array.from({length:12}).map((_,i)=><div className="catalog-poster skeleton" key={i}/>)}</div>:items.length?<PosterGrid items={items.map(item=>({...item,history:{position_ms:item.position_ms||0,completed:true,last_watched_at:item.last_watched_at}}))}/>:<div className="catalog-empty">NOTHING WATCHED YET</div>}</div>
}

function CollectionsPage(){
  const [collections,setCollections]=useState<Collection[]>(()=>peekCached<{collections:Collection[]}>('/api/movies/collections/list')?.collections||[]);const [selected,setSelected]=useState<string|null>(null);const [items,setItems]=useState<MediaItem[]>([]);const [loading,setLoading]=useState(!collections.length);const [error,setError]=useState('')
  useEffect(()=>{cachedApi<{collections:Collection[]}>('/api/movies/collections/list',120000).then(result=>setCollections(result.collections)).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[])
  useEffect(()=>{if(!selected){setItems([]);return}let cancelled=false;const path=`/api/movies?collection=${encodeURIComponent(selected)}&sort=alphabetical&limit=120`;setLoading(true);cachedApi<{items:MediaItem[]}>(path,60000).then(async result=>{if(!cancelled)setItems(await enrichItems(result.items))}).catch(e=>{if(!cancelled)setError(e.message)}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true}},[selected])
  if(selected)return <div className="page catalog-page"><button className="collection-back" onClick={()=>setSelected(null)}>← ALL COLLECTIONS</button><div className="catalog-heading"><div><h1>{selected}</h1><span>{items.length} TITLES</span></div></div>{loading&&!items.length?<div className="catalog-grid">{Array.from({length:12}).map((_,index)=><div className="catalog-poster skeleton" key={index}/>)}</div>:<PosterGrid items={items}/>}</div>
  return <div className="page catalog-page"><section className="catalog-hero collections-hero"><div className="terminal-kicker"><Library size={14}/> /PLEX_COLLECTIONS</div><h1>COLLECTIONS</h1><p>Curated groups imported directly from Plex metadata.</p></section>{error&&<div className="terminal-alert bad">{error}</div>}{loading&&!collections.length?<div className="collection-grid">{Array.from({length:8}).map((_,index)=><div className="collection-card skeleton" key={index}/>)}</div>:collections.length?<div className="collection-grid">{collections.map(collection=><button key={collection.name} className="collection-card" onClick={()=>setSelected(collection.name)}>{collection.poster_url?<img src={collection.poster_url} alt="" loading="lazy" decoding="async"/>:<div className="collection-art-empty"><Library/></div>}<div className="collection-shade"/><div className="collection-copy"><strong>{collection.name}</strong><span>{collection.count} TITLES · {collection.movie_count} MOVIES · {collection.show_count} SERIES</span></div><ChevronRight/></button>)}</div>:<div className="catalog-empty">NO PLEX COLLECTION TAGS HAVE BEEN INDEXED YET</div>}</div>
}

function formatRuntime(ms?:number){if(!ms)return null;const minutes=Math.round(ms/60000);return minutes>=60?`${Math.floor(minutes/60)}h ${minutes%60}m`:`${minutes}m`}
function EpisodeCard({episode}:{episode:MediaItem}){const watched=episode.history?.completed;const progress=progressPercent(episode);return <div className="episode-card"><Link to={`/media/${episode.id}`} className="episode-thumb" onPointerEnter={()=>prefetchMedia(episode.id)}>{episode.poster_url?<img src={episode.poster_url} alt="" loading="lazy" decoding="async"/>:<div className="episode-thumb-empty"><Tv/></div>}<span className="episode-number">E{episode.episode_number??'?'}</span>{watched&&<span className="watched-overlay"><CheckCircle2 size={12}/>WATCHED</span>}{progress>0&&!watched&&<div className="watch-progress"><span style={{width:`${progress}%`}}/></div>}</Link><div className="episode-card-copy"><div><strong>{episode.title}</strong><span>{formatRuntime(episode.duration_ms)}{bestQuality(episode.qualities)?` · ${bestQuality(episode.qualities)}`:''}</span></div><p>{episode.summary||'No episode description supplied by Plex.'}</p><Link className="episode-play" to={`/watch/${episode.id}`}><Play fill="currentColor" size={16}/>{progress>0?' CONTINUE':' PLAY'}</Link></div></div>}

function MediaDetailPage(){
  const {id}=useParams();const detailPath=`/api/movies/${id}`;const cachedDetail=peekCached<MediaItem>(detailPath);const [item,setItem]=useState<MediaItem|null>(cachedDetail||null);const [episodes,setEpisodes]=useState<MediaItem[]>([]);const [season,setSeason]=useState<number|null>(cachedDetail?.media_type==='show'&&cachedDetail.seasons?.length?cachedDetail.seasons[0].season_number??0:null);const [historyState,setHistoryState]=useState<HistoryState>({position_ms:0,completed:false,last_watched_at:null});const [loading,setLoading]=useState(!cachedDetail);const [episodesLoading,setEpisodesLoading]=useState(false);const [error,setError]=useState('');const [historyBusy,setHistoryBusy]=useState(false)
  useEffect(()=>{const path=`/api/movies/${id}`;setError('');cachedApi<MediaItem>(path,120000).then(async media=>{setItem((await enrichCatalogStatus([media]).catch(()=>[media]))[0]);if(media.media_type==='show'&&media.seasons?.length)setSeason(current=>current??media.seasons![0].season_number??0);setLoading(false)}).catch(e=>{setError(e.message);setLoading(false)});api<HistoryState>(`/api/history/${id}`).then(setHistoryState).catch(()=>{})},[id])
  useEffect(()=>{if(!item||item.media_type!=='show'||season===null)return;const path=`/api/movies/${item.id}/episodes?season_number=${season}`;let cancelled=false;setEpisodesLoading(true);cachedApi<{episodes:MediaItem[]}>(path,60000).then(async result=>{if(!cancelled)setEpisodes(await enrichItems(result.episodes))}).catch(e=>{if(!cancelled)setError(e.message)}).finally(()=>{if(!cancelled)setEpisodesLoading(false)});return()=>{cancelled=true}},[item?.id,item?.media_type,season])
  async function toggleWatched(){if(!item)return;setHistoryBusy(true);try{if(historyState.completed)await api(`/api/history/${item.id}/watched`,{method:'DELETE'});else await api(`/api/history/${item.id}/watched`,{method:'POST'});clearHistoryCache();setHistoryState(current=>({...current,position_ms:historyState.completed?0:(item.duration_ms||current.position_ms),completed:!historyState.completed,last_watched_at:new Date().toISOString()}))}catch(e:any){setError(e.message)}finally{setHistoryBusy(false)}}
  if(loading&&!item)return <div className="page"><div className="skeleton" style={{height:'70vh'}}/></div>;if(error&&!item)return <div className="page"><div className="terminal-alert bad">{error}</div></div>;if(!item)return null
  const playable=item.playable&&(item.media_type==='movie'||item.media_type==='episode');const ready=isDirectReady(item);const progress=item.duration_ms?Math.round(Math.min(100,(historyState.position_ms/item.duration_ms)*100)):0
  return <div className={`stream-detail ${item.is_anime?'anime-detail':''}`}>{item.backdrop_url&&<img className="stream-backdrop" src={item.backdrop_url} alt="" decoding="async"/>}<div className="stream-detail-shade"/><div className="stream-detail-content"><div className="terminal-kicker"><Terminal size={14}/> {item.is_anime?'ANIME':'MEDIA'} // {item.library?.server_name||'PLEX'}</div><div className="stream-pills"><span>{item.is_anime?'ANIME':item.media_type.toUpperCase()}</span>{item.year&&<span>{item.year}</span>}{item.rating&&<span>{item.rating}</span>}{item.qualities?.map(quality=><span key={quality}>{quality}</span>)}{ready&&<span className="ready-pill">READY</span>}{historyState.completed&&<span className="watched-pill">WATCHED</span>}</div><h1>{item.title}</h1><p>{item.summary}</p><div className="stream-meta">{formatRuntime(item.duration_ms)}{item.genres?.length?` · ${item.genres.slice(0,4).join(' · ')}`:''}{progress>0&&!historyState.completed?` · ${progress}% watched`:''}</div>{playable&&<div className="stream-actions"><Link className="netflix-play" to={`/watch/${item.id}`}><Play fill="currentColor" size={24}/>{historyState.position_ms>0&&!historyState.completed?' CONTINUE':' PLAY'}</Link><button className={`watch-state-button ${historyState.completed?'watched':''}`} disabled={historyBusy} onClick={toggleWatched}>{historyState.completed?<RotateCcw size={17}/>:<Eye size={17}/>} {historyState.completed?'MARK UNWATCHED':'MARK WATCHED'}</button><VrChatButton mediaId={item.id} title={item.title}/></div>}</div>{item.media_type==='show'&&<section className="series-browser"><div className="series-browser-head"><div><h2>EPISODES</h2><span>{item.season_count||0} SEASONS · {item.episode_count||0} EPISODES</span></div><select value={season??0} onChange={event=>setSeason(Number(event.target.value))}>{item.seasons?.map(showSeason=><option key={showSeason.id} value={showSeason.season_number??0}>{showSeason.season_number===0?'Specials':`Season ${showSeason.season_number}`} · {showSeason.episode_count||0} episodes</option>)}</select></div>{episodesLoading&&!episodes.length?<div className="episode-grid">{Array.from({length:6}).map((_,index)=><div className="episode-card skeleton" key={index}/>)}</div>:<div className="episode-grid">{episodes.map(episode=><EpisodeCard episode={episode} key={episode.id}/>)}</div>}</section>}{error&&<div className="terminal-alert bad detail-error">{error}</div>}</div>
}

export function CatalogApp(){
  const location=useLocation();const [user,setUser]=useState<User|null|undefined>(undefined);useEffect(()=>{api<User>('/api/auth/me').then(setUser).catch(()=>setUser(null))},[])
  const content=useMemo(()=>{if(location.pathname==='/search')return <CatalogPage searchOnly/>;if(location.pathname==='/categories')return <CategoriesPage/>;if(location.pathname==='/collections')return <CollectionsPage/>;if(location.pathname==='/watched')return <WatchedPage/>;if(/^\/(media|movie)\/\d+$/.test(location.pathname))return <MediaDetailPage/>;return <CatalogPage/>},[location.pathname])
  if(user===undefined)return <div className="catalog-boot"><div className="watch-spinner"/>LOADING CATALOG…</div>;if(user===null){window.location.href='/login';return null};return <Shell user={user}>{content}</Shell>
}
