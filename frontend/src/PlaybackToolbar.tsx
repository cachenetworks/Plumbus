import { Copy, ExternalLink, MonitorPlay, RadioTower, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'

import './playback-toolbar.css'

type VrChatResponse={
  vrchat_url:string
  playback_url:string
  delivery:'progressive'|'hls'
  expires_at:string
  compatibility:string
  media?:{resolution?:string;video_codec?:string;container?:string}
}

type MediaState={playable?:boolean;media_type?:string}

async function postVrChat(id:number):Promise<VrChatResponse>{
  const response=await fetch(`/api/playback/media/${id}/vrchat`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'}})
  if(!response.ok){
    const body=await response.json().catch(()=>({detail:`HTTP ${response.status}`}))
    const detail=typeof body?.detail==='string'?body.detail:JSON.stringify(body?.detail||body)
    throw new Error(detail)
  }
  return response.json()
}

export function PlaybackToolbar(){
  const location=useLocation()
  const mediaId=useMemo(()=>Number(location.pathname.match(/^\/media\/(\d+)$/)?.[1]||0),[location.pathname])
  const [playable,setPlayable]=useState(false)
  const [result,setResult]=useState<VrChatResponse|null>(null)
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(false)
  const [copied,setCopied]=useState(false)

  useEffect(()=>{
    setResult(null);setError('');setCopied(false);setPlayable(false)
    if(!mediaId)return
    fetch(`/api/movies/${mediaId}`,{credentials:'include'})
      .then(async response=>response.ok?response.json():Promise.reject())
      .then((media:MediaState)=>setPlayable(Boolean(media.playable&&(media.media_type==='movie'||media.media_type==='episode'))))
      .catch(()=>setPlayable(false))
  },[mediaId])
  if(!mediaId||!playable)return null

  async function generate(){
    setLoading(true);setError('');setCopied(false)
    try{setResult(await postVrChat(mediaId))}catch(e:any){setError(e.message)}finally{setLoading(false)}
  }

  async function copy(){
    if(!result)return
    await navigator.clipboard.writeText(result.vrchat_url)
    setCopied(true);setTimeout(()=>setCopied(false),1600)
  }

  return <>
    <div className="playback-float">
      <a className="playback-float-btn primary" href={`/watch/${mediaId}`}><MonitorPlay size={18}/> WATCH IN BROWSER</a>
      <button className="playback-float-btn" disabled={loading} onClick={generate}><RadioTower size={18}/>{loading?'GENERATING...':'GET VRCHAT LINK'}</button>
    </div>
    {(result||error)&&<div className="vrchat-modal-backdrop" onClick={()=>{setResult(null);setError('')}}>
      <div className="vrchat-modal" onClick={e=>e.stopPropagation()}>
        <div className="vrchat-modal-head"><div><span>AVPRO</span> VRCHAT STREAM ROUTE</div><button onClick={()=>{setResult(null);setError('')}}><X size={20}/></button></div>
        {error?<div className="vrchat-error">{error}</div>:result&&<div className="vrchat-modal-body">
          <div className="vrchat-status-row"><span className="vrchat-status good">ACTIVE</span><code>{result.delivery.toUpperCase()}</code><code>{result.media?.resolution||'AUTO'}</code></div>
          <p>{result.compatibility}</p>
          <label>PASTE THIS URL INTO YOUR VRCHAT AVPRO / UDON VIDEO PLAYER</label>
          <textarea readOnly value={result.vrchat_url} onFocus={e=>e.currentTarget.select()}/>
          <div className="vrchat-actions"><button onClick={copy}><Copy size={17}/>{copied?'COPIED':'COPY VRCHAT LINK'}</button><a href={result.vrchat_url} target="_blank" rel="noreferrer"><ExternalLink size={17}/> TEST LINK</a></div>
          <small>Expires {new Date(result.expires_at).toLocaleString()}. The Plex access token stays server-side.</small>
        </div>}
      </div>
    </div>}
  </>
}
