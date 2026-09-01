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

type AudioTrack = {
  id:string
  codec?:string
  language?:string
  language_code?:string
  language_tag?:string
  channels?:number
  selected?:boolean
  default?:boolean
  title?:string
  display_title?:string
  extended_display_title?:string
}

type PlaybackResponse = {
  media_id:number
  target:'browser'
  playback_url:string
  delivery:'progressive'|'hls'
  expires_at:string
  resume_position_ms:number
  stream_mode?:'direct'|'direct_stream'|'audio'|'compatibility'
  browser_codec_profile?:string
  audio_strategy?:'original_file'|'copy_existing_track'|'compatibility_encode'
  audio_warning?:string
  audio_tracks?:AudioTrack[]
  selected_audio_stream_id?:string|null
  media:{playback_mode?:string;resolution?:string;video_codec?:string;audio_codec?:string;container?:string}
}

type AudioTracksResponse={audio_tracks:AudioTrack[];selected_audio_stream_id?:string|null;can_direct_switch:boolean}
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

function audioTrackLabel(track:AudioTrack):string {
  const language=track.language||track.language_code||track.language_tag||'Unknown'
  const codec=(track.codec||'audio').toUpperCase()
  const channels=track.channels?` ${track.channels}ch`:''
  const title=track.title&&!track.title.toLowerCase().includes(language.toLowerCase())?` · ${track.title}`:''
  return `${language} · ${codec}${channels}${title}`
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
  const compatibilityAttemptedRef=useRef(false)
  const pendingSeekRef=useRef<number|null>(null)
  const hideTimerRef=useRef<number|null>(null)
  const [item,setItem]=useState<MediaItem|null>(null)
  const [playback,setPlayback]=useState<PlaybackResponse|null>(null)
  const [navigation,setNavigation]=useState<NavigationResponse>({previous:null,next:null})
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(true)
  const [playing,setPlaying]=useState(false)
  const [hasFrame,setHasFrame]=useState(false)
  const [current,setCurrent]=useState(0)
  const [duration,setDuration]=useState(0)
  const [volume,setVolume]=useState(1)
  const [muted,setMuted]=useState(false)
  const [controlsVisible,setControlsVisible]=useState(true)
  const [audioSwitching,setAudioSwitching]=useState(false)
  const [audioLoading,setAudioLoading]=useState(false)
  const [audioMenuOpen,setAudioMenuOpen]=useState(false)
  const [canDirectSwitch,setCanDirectSwitch]=useState<boolean|undefined>(undefined)

  useEffect(()=>{
    if(!id){setError('Invalid media id');setLoading(false);return}
    setLoading(true);setError('');setItem(null);setPlayback(null);setNavigation({previous:null,next:null});setHasFrame(false);resumedRef.current=false;compatibilityAttemptedRef.current=false;pendingSeekRef.current=null;lastSavedRef.current=0;setAudioMenuOpen(false);setCanDirectSwitch(undefined)

    void jsonApi<MediaItem>(`/api/movies/${id}`)
      .then(setItem)
      .catch(e=>setError(`Media metadata: ${e.message}`))

    void jsonApi<NavigationResponse>(`/api/playback/media/${id}/navigation`)
      .then(setNavigation)
      .catch(()=>setNavigation({previous:null,next:null}))

    jsonApi<PlaybackResponse>(`/api/playback/media/${id}/browser?mode=direct`,{method:'POST'})
      .then(target=>{setPlayback(target);setLoading(false)})
      .catch(e=>{setError(e.message);setLoading(false)})
  },[id])

  useEffect(()=>{
    const video=videoRef.current
    if(!video||!playback)return
    setHasFrame(false)
    hlsRef.current?.destroy();hlsRef.current=null
    video.removeAttribute('src');video.load()

    if(playback.delivery==='hls'&&Hls.isSupported()){
      const hls=new Hls({
        enableWorker:true,
        lowLatencyMode:false,
        backBufferLength:60,
        maxBufferLength:90,
        maxMaxBufferLength:240,
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
        if(!data.fatal)return
        if((playback.stream_mode==='direct_stream'||playback.stream_mode==='audio')&&!compatibilityAttemptedRef.current){
          void handleVideoError()
          return
        }
        setError(`Playback stream error: ${data.details}`)
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

    if(pendingSeekRef.current!==null){
      const target=Math.max(0,pendingSeekRef.current)
      if(!video.duration||target<video.duration-1)video.currentTime=target
      setCurrent(target)
      pendingSeekRef.current=null
      resumedRef.current=true
      void video.play().catch(()=>{})
      return
    }

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
    setHasFrame(true)
    setCurrent(video.currentTime||0)
    if(video.duration)setDuration(video.duration)
    void saveProgress(false)
  }

  async function compatibilityPlayback(){
    compatibilityAttemptedRef.current=true
    pendingSeekRef.current=videoRef.current?.currentTime||current||0
    try{
      const fallback=await jsonApi<PlaybackResponse>(`/api/playback/media/${id}/browser?mode=compatibility`,{method:'POST'})
      setError('')
      setPlayback(fallback)
    }catch(e:any){
      setError(`Plex compatibility playback could not start: ${e.message}`)
    }
  }

  async function handleVideoError(){
    if((playback?.stream_mode==='direct'||playback?.stream_mode==='direct_stream'||playback?.stream_mode==='audio')&&!compatibilityAttemptedRef.current){
      await compatibilityPlayback()
      return
    }
    setError('The browser could not decode this Plex stream. Try another source/audio track or use the VRChat link mode.')
  }

  async function loadAudioTracks(){
    if(playback?.audio_tracks){
      setAudioMenuOpen(value=>!value)
      return
    }
    setAudioLoading(true)
    try{
      const response=await jsonApi<AudioTracksResponse>(`/api/playback/media/${id}/audio-tracks`)
      setPlayback(currentPlayback=>currentPlayback?{
        ...currentPlayback,
        audio_tracks:response.audio_tracks,
        selected_audio_stream_id:currentPlayback.selected_audio_stream_id||response.selected_audio_stream_id,
      }:currentPlayback)
      setCanDirectSwitch(response.can_direct_switch)
      setAudioMenuOpen(true)
      setError('')
    }catch(e:any){
      setError(`Unable to load Plex audio tracks: ${e.message}`)
    }finally{
      setAudioLoading(false)
    }
  }

  async function switchAudioTrack(streamId:string){
    if(!streamId||streamId===playback?.selected_audio_stream_id)return
    const track=playback?.audio_tracks?.find(candidate=>candidate.id===streamId)
    if(!track)return
    const videoCodec=(playback?.media.video_codec||'').toLowerCase()
    const audioCodec=(track.codec||'').toLowerCase()
    if(!['h264','avc'].includes(videoCodec)||!['aac','mp3'].includes(audioCodec)){
      setError('That track cannot be switched without encoding. Direct Stream supports AAC/MP3 audio on H.264 video.')
      return
    }

    setAudioSwitching(true)
    pendingSeekRef.current=videoRef.current?.currentTime||current||0
    try{
      const next=await jsonApi<PlaybackResponse>(`/api/playback/media/${id}/browser?mode=direct&audio_stream_id=${encodeURIComponent(streamId)}`,{method:'POST'})
      compatibilityAttemptedRef.current=false
      setError('')
      setPlayback(next)
      setCanDirectSwitch(true)
      setAudioMenuOpen(true)
    }catch(e:any){
      setError(`Unable to switch Plex audio track: ${e.message}`)
    }finally{
      setAudioSwitching(false)
    }
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

  if(loading)return <div className="watch-loading"><div className="watch-spinner"/><span>CONNECTING DIRECTLY TO PLEX...</span></div>
  if(error&&!playback)return <div className="watch-error"><button onClick={exitPlayer}><ChevronLeft size={18}/> BACK</button><div><strong>PLAYBACK FAILED</strong><p>{error}</p></div></div>

  const deliveryLabel=playback?.stream_mode==='direct'
    ?'DIRECT PLEX'
    :playback?.stream_mode==='direct_stream'
      ?'DIRECT STREAM // NO ENCODE'
      :playback?.stream_mode==='audio'
        ?'VIDEO COPY + AAC AUDIO'
        :'COMPATIBILITY'
  const audioTracks=playback?.audio_tracks||[]
  const videoDirectSwitch=canDirectSwitch??['h264','avc'].includes((playback?.media.video_codec||'').toLowerCase())

  return <div ref={shellRef} className={`watch-shell ${controlsVisible?'controls-visible':''}`} onMouseMove={showControls} onClick={showControls}>
    <video
      ref={videoRef}
      className="watch-video"
      playsInline
      preload="auto"
      onLoadedMetadata={onLoadedMetadata}
      onLoadedData={()=>setHasFrame(true)}
      onTimeUpdate={onTimeUpdate}
      onPlay={()=>{setPlaying(true);showControls()}}
      onPause={()=>{setPlaying(false);setControlsVisible(true);void saveProgress(true)}}
      onEnded={()=>{setPlaying(false);void jsonApi('/api/history/complete',{method:'POST',body:JSON.stringify({movie_id:id,position_ms:Math.floor((videoRef.current?.duration||0)*1000)})}).catch(()=>{});if(navigation.next)setTimeout(()=>switchEpisode(navigation.next),1200)}}
      onVolumeChange={()=>{const video=videoRef.current;if(video){setVolume(video.volume);setMuted(video.muted)}}}
      onError={()=>{void handleVideoError()}}
    />
    {item?.backdrop_url&&!hasFrame&&<div className="watch-backdrop" style={{backgroundImage:`url(${item.backdrop_url})`}}/>}
    <div className="watch-vignette"/>

    <div className="watch-topbar">
      <button className="watch-icon-btn" onClick={exitPlayer} aria-label="Exit player"><X size={28}/></button>
      <div className="watch-brand">PLUMBUS <span>// WEB PLAYER</span></div>
      {playback&&<div className="watch-delivery">{deliveryLabel} // {playback.media?.resolution||'AUTO'}</div>}
    </div>

    <button className="watch-center-play" onClick={togglePlay} aria-label={playing?'Pause':'Play'}>{playing?<Pause size={48}/>:<Play size={52}/>}</button>

    <div className="watch-controls">
      <div className="watch-title-block">
        {item?.grandparent_title&&<div className="watch-series">{item.grandparent_title}</div>}
        <strong>{item?.title}</strong>
        {item?.media_type==='episode'&&<span>S{String(item.season_number??0).padStart(2,'0')} E{String(item.episode_number??0).padStart(2,'0')}</span>}
      </div>
      {playback?.audio_warning&&<div className="watch-audio-warning"><span>{playback.audio_warning}</span><button onClick={()=>{void compatibilityPlayback()}}>COMPATIBILITY</button></div>}
      {error&&<div className="watch-inline-error">{error}</div>}
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
        <div className="watch-audio-menu-wrap">
          <button className="watch-audio-button" disabled={audioLoading} onClick={()=>{void loadAudioTracks()}}>{audioLoading?'AUDIO…':'AUDIO'}</button>
          {audioMenuOpen&&<div className="watch-audio-menu">{audioTracks.length?<><div className="watch-audio-menu-title">PLEX AUDIO TRACKS</div><select aria-label="Audio track" disabled={audioSwitching||!videoDirectSwitch} value={playback?.selected_audio_stream_id||''} onChange={e=>{void switchAudioTrack(e.target.value)}}>{audioTracks.map(track=>{const supported=videoDirectSwitch&&['aac','mp3'].includes((track.codec||'').toLowerCase());return <option key={track.id} value={track.id} disabled={!supported}>{audioTrackLabel(track)}{supported?'':' · encode required'}</option>})}</select><small>{videoDirectSwitch?'AAC/MP3 tracks switch with no codec encoding.':'This video codec cannot switch embedded tracks in-browser without compatibility mode.'}</small></>:<span>NO AUDIO TRACKS REPORTED BY PLEX</span>}</div>}
        </div>
        <div className="watch-spacer"/>
        {navigation.previous&&<button className="watch-episode-btn" onClick={()=>switchEpisode(navigation.previous)}><ChevronLeft size={20}/> PREV EP</button>}
        {navigation.next&&<button className="watch-episode-btn" onClick={()=>switchEpisode(navigation.next)}>NEXT EP <ChevronRight size={20}/></button>}
        <button onClick={fullscreen} title="Fullscreen"><Maximize size={24}/></button>
      </div>
    </div>
  </div>
}
