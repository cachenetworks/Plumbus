import Hls from 'hls.js'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Maximize, Pause, Play, SkipBack, SkipForward, Volume2, VolumeX, X } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'

import './watch.css'

type MediaItem = {
  id:number
  media_type:'movie'|'show'|'season'|'episode'
  title:string
  summary?:string
  year?:number
  backdrop_url?:string
  poster_url?:string
  duration_ms?:number
  season_number?:number
  episode_number?:number
  parent_title?:string
  grandparent_title?:string
  library?:{title:string;server_name?:string}
}

type PlaybackResponse = {
  media_id:number
  target:'browser'
  playback_url:string
  delivery:'progressive'|'hls'
  expires_at:string
  resume_position_ms:number
  media:{playback_mode?:string;resolution?:string;video_codec?:string;container?:string}
}

type EpisodeSummary = { id:number; title:string; season_number?:number; episode_number?:number }
type NavigationResponse = { previous:EpisodeSummary|null; next:EpisodeSummary|null; series_title?:string }

async function jsonApi<T>(path:string, init?:RequestInit):Promise<T> {
  const response=await fetch(path,{credentials:'include',headers:{'Content-Type':'application/json',...(init?.headers||{})},...init})
  if(!response.ok){
    const body=await response.json().catch(()=>({detail:`HTTP ${response.status}`}))
    const detail=typeof body?.detail==='string'?body.detail:JSON.stringify(body?.detail||body)
    throw new Error(detail)
  }
  return response.json()
}

function formatTime(seconds:number):string {
  if(!Number.isFinite(seconds)||seconds<0)return '0:00'
  const total=Math.floor(seconds)
  const h=Math.floor(total/3600)
  const m=Math.floor((total%3600)/60)
  const s=total%60
  return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`
}

export function WatchPage(){
  const location=useLocation()
  const navigate=useNavigate()
  const id=useMemo(()=>Number(location.pathname.match(/^\/watch\/(\d+)/)?.[1]||0),[location.pathname])
  const videoRef=useRef<HTMLVideoElement|null>(null)
  const shellRef=useRef<HTMLDivElement|null>(null)
  const hlsRef=useRef<Hls|null>(null)
  const lastSavedRef=useRef(0)
  const resumedRef=useRef(false)
  const hideTimerRef=useRef<number|null>(null)
  const [item,setItem]=useState<MediaItem|null>(null)
  const [playback,setPlayback]=useState<PlaybackResponse|null>(null)
  const [navigation,setNavigation]=useState<NavigationResponse>({previous:null,next:null})
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(true)
  const [playing,setPlaying]=useState(false)
  const [current,setCurrent]=useState(0)
  const [duration,setDuration]=useState(0)
  const [volume,setVolume]=useState(1)
  const [muted,setMuted]=useState(false)
  const [controlsVisible,setControlsVisible]=useState(true)

  useEffect(()=>{
    if(!id){setError('Invalid media id');setLoading(false);return}
    setLoading(true);setError('');setItem(null);setPlayback(null);resumedRef.current=false;lastSavedRef.current=0
    Promise.all([
      jsonApi<MediaItem>(`/api/movies/${id}`),
      jsonApi<NavigationResponse>(`/api/playback/media/${id}/navigation`),
      jsonApi<PlaybackResponse>(`/api/playback/media/${id}/browser`,{method:'POST'}),
    ]).then(([metadata,nav,target])=>{
      setItem(metadata);setNavigation(nav);setPlayback(target);setLoading(false)
    }).catch(e=>{setError(e.message);setLoading(false)})
  },[id])

  useEffect(()=>{
    const video=videoRef.current
    if(!video||!playback)return
    hlsRef.current?.destroy();hlsRef.current=null
    video.removeAttribute('src');video.load()

    if(playback.delivery==='hls'&&Hls.isSupported()){
      const hls=new Hls({
        enableWorker:true,
        lowLatencyMode:false,
        backBufferLength:60,
        maxBufferLength:60,
        maxMaxBufferLength:180,
        maxBufferHole:.75,
        startFragPrefetch:true,
        fragLoadingMaxRetry:6,
        manifestLoadingMaxRetry:4,
        levelLoadingMaxRetry:4,
      })
      hlsRef.current=hls
      hls.loadSource(playback.playback_url)
      hls.attachMedia(video)
      hls.on(Hls.Events.ERROR,(_event,data)=>{
        if(data.fatal)setError(`Playback error: ${data.details}`)
      })
    }else{
      video.src=playback.playback_url
    }

    return()=>{hlsRef.current?.destroy();hlsRef.current=null}
  },[playback])

  async function saveProgress(force=false){
    const video=videoRef.current
    if(!video||!id||!Number.isFinite(video.currentTime))return
    const ms=Math.max(0,Math.floor(video.currentTime*1000))
    if(!force&&Math.abs(ms-lastSavedRef.current)<15000)return
    lastSavedRef.current=ms
    const nearEnd=video.duration>0&&video.currentTime/video.duration>=0.95
    try{
      await jsonApi(nearEnd?'/api/history/complete':'/api/history/progress',{
        method:'POST',body:JSON.stringify({movie_id:id,position_ms:ms}),
      })
    }catch{/* playback should not stop because a history checkpoint failed */}
  }

  function onLoadedMetadata(){
    const video=videoRef.current
    if(!video||!playback)return
    setDuration(video.duration||0)
    const resume=playback.resume_position_ms/1000
    if(!resumedRef.current&&resume>5&&(!video.duration||resume<video.duration-10)){
      video.currentTime=resume
      setCurrent(resume)
    }
    resumedRef.current=true
    void video.play().catch(()=>{})
  }

  function onTimeUpdate(){
    const video=videoRef.current
    if(!video)return
    setCurrent(video.currentTime||0)
    if(video.duration)setDuration(video.duration)
    void saveProgress(false)
  }

  function togglePlay(){
    const video=videoRef.current;if(!video)return
    if(video.paused)void video.play();else video.pause()
  }

  function seekTo(value:number){
    const video=videoRef.current;if(!video)return
    video.currentTime=value;setCurrent(value);void saveProgress(true)
  }

  function skip(delta:number){
    const video=videoRef.current;if(!video)return
    seekTo(Math.max(0,Math.min(video.duration||Infinity,video.currentTime+delta)))
  }

  function setPlayerVolume(value:number){
    const video=videoRef.current;if(!video)return
    video.volume=value;video.muted=value===0;setVolume(value);setMuted(value===0)
  }

  function toggleMute(){
    const video=videoRef.current;if(!video)return
    video.muted=!video.muted;setMuted(video.muted)
  }

  async function fullscreen(){
    if(!document.fullscreenElement)await shellRef.current?.requestFullscreen();else await document.exitFullscreen()
  }

  function showControls(){
    setControlsVisible(true)
    if(hideTimerRef.current)window.clearTimeout(hideTimerRef.current)
    hideTimerRef.current=window.setTimeout(()=>{if(!videoRef.current?.paused)setControlsVisible(false)},2600)
  }

  function switchEpisode(target:EpisodeSummary|null){
    if(!target)return
    void saveProgress(true)
    navigate(`/watch/${target.id}`)
  }

  function exitPlayer(){
    void saveProgress(true)
    navigate(`/media/${id}`)
  }

  useEffect(()=>()=>{if(hideTimerRef.current)window.clearTimeout(hideTimerRef.current)},[])

  if(loading)return <div className="watch-loading"><div className="watch-spinner"/><span>BUFFERING MEDIA ROUTE...</span></div>
  if(error)return <div className="watch-error"><button onClick={exitPlayer}><ChevronLeft size={18}/> BACK</button><div><strong>PLAYBACK FAILED</strong><p>{error}</p></div></div>

  return <div ref={shellRef} className={`watch-shell ${controlsVisible?'controls-visible':''}`} onMouseMove={showControls} onClick={showControls}>
    <video
      ref={videoRef}
      className="watch-video"
      playsInline
      preload="auto"
      onLoadedMetadata={onLoadedMetadata}
      onTimeUpdate={onTimeUpdate}
      onPlay={()=>{setPlaying(true);showControls()}}
      onPause={()=>{setPlaying(false);setControlsVisible(true);void saveProgress(true)}}
      onEnded={()=>{setPlaying(false);void jsonApi('/api/history/complete',{method:'POST',body:JSON.stringify({movie_id:id,position_ms:Math.floor((videoRef.current?.duration||0)*1000)})}).catch(()=>{});if(navigation.next)setTimeout(()=>switchEpisode(navigation.next),1200)}}
      onVolumeChange={()=>{const video=videoRef.current;if(video){setVolume(video.volume);setMuted(video.muted)}}}
      onError={()=>setError('The browser could not decode this stream. Try enabling Plex transcoding or use the VRChat link mode.')}
    />
    {item?.backdrop_url&&!playing&&<div className="watch-backdrop" style={{backgroundImage:`url(${item.backdrop_url})`}}/>}
    <div className="watch-vignette"/>

    <div className="watch-topbar">
      <button className="watch-icon-btn" onClick={exitPlayer} aria-label="Exit player"><X size={28}/></button>
      <div className="watch-brand">PLUMBUS <span>// WEB PLAYER</span></div>
      {playback&&<div className="watch-delivery">{playback.delivery.toUpperCase()} // {playback.media?.resolution||'AUTO'}</div>}
    </div>

    <button className="watch-center-play" onClick={togglePlay} aria-label={playing?'Pause':'Play'}>{playing?<Pause size={48}/>:<Play size={52}/>}</button>

    <div className="watch-controls">
      <div className="watch-title-block">
        {item?.grandparent_title&&<div className="watch-series">{item.grandparent_title}</div>}
        <strong>{item?.title}</strong>
        {item?.media_type==='episode'&&<span>S{String(item.season_number??0).padStart(2,'0')} E{String(item.episode_number??0).padStart(2,'0')}</span>}
      </div>
      <div className="watch-progress-row">
        <span>{formatTime(current)}</span>
        <input aria-label="Playback position" type="range" min={0} max={Math.max(duration,1)} step="0.1" value={Math.min(current,Math.max(duration,1))} onChange={e=>seekTo(Number(e.target.value))}/>
        <span>{formatTime(duration)}</span>
      </div>
      <div className="watch-button-row">
        <button onClick={togglePlay}>{playing?<Pause size={24}/>:<Play size={24}/>}</button>
        <button onClick={()=>skip(-10)} title="Back 10 seconds"><SkipBack size={22}/><small>10</small></button>
        <button onClick={()=>skip(10)} title="Forward 10 seconds"><SkipForward size={22}/><small>10</small></button>
        <button onClick={toggleMute}>{muted||volume===0?<VolumeX size={24}/>:<Volume2 size={24}/>}</button>
        <input className="watch-volume" aria-label="Volume" type="range" min={0} max={1} step="0.05" value={muted?0:volume} onChange={e=>setPlayerVolume(Number(e.target.value))}/>
        <div className="watch-spacer"/>
        {navigation.previous&&<button className="watch-episode-btn" onClick={()=>switchEpisode(navigation.previous)}><ChevronLeft size={20}/> PREV EP</button>}
        {navigation.next&&<button className="watch-episode-btn" onClick={()=>switchEpisode(navigation.next)}>NEXT EP <ChevronRight size={20}/></button>}
        <button onClick={fullscreen} title="Fullscreen"><Maximize size={24}/></button>
      </div>
    </div>
  </div>
}
