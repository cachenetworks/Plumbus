from __future__ import annotations

import logging
import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import ApplicationSetting, PlexLibrary, PlexServer
from app.security.secrets import decrypt_secret, encrypt_secret
from app.security.security import token_hash
from app.services.configuration import IntegrationConfigurationService
from app.services.plex.account import PlexAccountService
from app.services.plex.service import PlexService
from app.services.settings import ApplicationSettingsService

log = logging.getLogger("plumbus.setup")
router = APIRouter(tags=["setup"])
SETUP_COOKIE = "plumbus_setup"
serializer = URLSafeTimedSerializer(settings.SESSION_SECRET, salt="plumbus-first-run")


class ClaimPayload(BaseModel):
    code: str = Field(min_length=6, max_length=128)


class SitePayload(BaseModel):
    app_url: str = Field(min_length=8, max_length=512)
    site_name: str = Field(default="Plumbus Cinema", min_length=1, max_length=100)


class DiscordPayload(BaseModel):
    client_id: str = Field(min_length=5, max_length=64)
    client_secret: str = Field(default="", max_length=256)
    owner_discord_id: str = Field(min_length=5, max_length=32)


class ServerPayload(BaseModel):
    client_identifier: str = Field(min_length=1, max_length=200)
    connection_uri: str = Field(min_length=8, max_length=1024)


class LibrariesPayload(BaseModel):
    enabled_keys: list[str] = Field(default_factory=list, max_length=200)


class PlaybackPayload(BaseModel):
    preferred_video_codec: str = Field(default="h264", max_length=32)
    preferred_resolution: str = Field(default="1080p", max_length=32)
    max_stream_bitrate_kbps: int = Field(default=20000, ge=500, le=200000)
    allow_plex_transcoding: bool = False


def _setup_complete(db: Session) -> bool:
    row = db.get(ApplicationSetting, "setup_state")
    return bool(row and isinstance(row.value, dict) and row.value.get("completed"))


def _claim_code(db: Session) -> str:
    row = db.get(ApplicationSetting, "setup_claim")
    if row and isinstance(row.value, dict) and row.value.get("code"):
        return decrypt_secret(str(row.value["code"]))
    code = secrets.token_hex(5).upper()
    row = ApplicationSetting(key="setup_claim", value={"code": encrypt_secret(code), "hash": token_hash(code)})
    db.add(row)
    db.commit()
    log.warning("PLUMBUS FIRST-RUN SETUP CODE: %s", code)
    return code


def _claimed_session(cookie: str | None) -> bool:
    if not cookie:
        return False
    try:
        return serializer.loads(cookie, max_age=3600) == "setup"
    except (BadSignature, SignatureExpired):
        return False


def require_setup(
    plumbus_setup: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Session:
    if _setup_complete(db):
        raise HTTPException(410, "Setup has already been completed")
    if not _claimed_session(plumbus_setup):
        raise HTTPException(401, "Enter the first-run setup code")
    return db


def _readiness(db: Session) -> dict:
    integration = IntegrationConfigurationService(db)
    site = integration.site()
    discord = integration.discord()
    plex_server = db.scalar(select(PlexServer).where(PlexServer.enabled.is_(True)).limit(1))
    enabled_libraries = db.scalar(
        select(PlexLibrary.id).where(PlexLibrary.enabled.is_(True)).limit(1)
    )
    checks = {
        "site_url": bool(site.app_url and urlparse(site.app_url).scheme in {"http", "https"}),
        "discord": discord.configured,
        "plex_account": bool(PlexAccountService(db).account_token(refresh=False)),
        "plex_server": bool(plex_server and plex_server.base_url not in {"", "environment"}),
        "libraries": enabled_libraries is not None,
    }
    return {"ready": all(checks.values()), "checks": checks}


@router.get("/setup", response_class=HTMLResponse)
def setup_page(db: Session = Depends(get_db)) -> HTMLResponse:
    if _setup_complete(db):
        return HTMLResponse("<script>location.href='/';</script>")
    _claim_code(db)
    return HTMLResponse(SETUP_HTML)


@router.get("/api/setup/status")
def setup_status(request: Request, db: Session = Depends(get_db)) -> dict:
    if _setup_complete(db):
        return {"completed": True, "claimed": False}
    code = _claim_code(db)
    # Re-log on each first-run status request so the installer can recover it from recent logs.
    log.warning("PLUMBUS FIRST-RUN SETUP CODE: %s", code)
    return {
        "completed": False,
        "claimed": _claimed_session(request.cookies.get(SETUP_COOKIE)),
    }


@router.post("/api/setup/claim")
def claim_setup(payload: ClaimPayload, response: Response, db: Session = Depends(get_db)) -> dict:
    if _setup_complete(db):
        raise HTTPException(410, "Setup has already been completed")
    row = db.get(ApplicationSetting, "setup_claim")
    if not row or not isinstance(row.value, dict):
        raise HTTPException(409, "Setup claim has not been initialized")
    if not secrets.compare_digest(str(row.value.get("hash") or ""), token_hash(payload.code.strip().upper())):
        raise HTTPException(403, "Incorrect setup code")
    response.set_cookie(
        SETUP_COOKIE,
        serializer.dumps("setup"),
        max_age=3600,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )
    return {"claimed": True}


@router.get("/api/setup/config")
def read_config(db: Session = Depends(require_setup)) -> dict:
    integration = IntegrationConfigurationService(db)
    site = integration.site()
    discord = integration.discord()
    plex = PlexAccountService(db)
    playback = ApplicationSettingsService(db).playback()
    return {
        "site": {"app_url": site.app_url, "site_name": site.site_name},
        "discord": {
            "client_id": discord.client_id,
            "client_secret_configured": bool(discord.client_secret),
            "owner_discord_id": discord.initial_superadmin_discord_id,
            "redirect_uri": discord.redirect_uri,
        },
        "plex_linked": bool(plex.account_token(refresh=False)),
        "playback": playback,
        "readiness": _readiness(db),
    }


@router.put("/api/setup/site")
def setup_site(payload: SitePayload, db: Session = Depends(require_setup)) -> dict:
    parsed = urlparse(payload.app_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "Enter a complete site URL including http:// or https://")
    site = IntegrationConfigurationService(db).set_site(payload.app_url, payload.site_name)
    db.commit()
    return {"app_url": site.app_url, "site_name": site.site_name}


@router.put("/api/setup/discord")
def setup_discord(payload: DiscordPayload, db: Session = Depends(require_setup)) -> dict:
    if not payload.client_id.isdigit() or not payload.owner_discord_id.isdigit():
        raise HTTPException(400, "Discord client ID and owner Discord ID must be numeric")
    try:
        config = IntegrationConfigurationService(db).set_discord(
            payload.client_id,
            payload.client_secret,
            payload.owner_discord_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"configured": config.configured, "redirect_uri": config.redirect_uri}


@router.post("/api/setup/plex/sign-in")
def plex_sign_in(db: Session = Depends(require_setup)) -> dict:
    site = IntegrationConfigurationService(db).site()
    result = PlexAccountService(db).start_sign_in(f"{site.app_url}/setup")
    db.commit()
    return result


@router.get("/api/setup/plex/sign-in/{pin_id}")
def plex_sign_in_poll(pin_id: int, db: Session = Depends(require_setup)) -> dict:
    try:
        result = PlexAccountService(db).poll_sign_in(pin_id)
    except Exception as exc:
        raise HTTPException(502, f"Plex sign-in check failed: {exc}") from exc
    db.commit()
    return result


@router.get("/api/setup/plex/servers")
def plex_servers(db: Session = Depends(require_setup)) -> list[dict]:
    try:
        resources = PlexAccountService(db).resources()
    except Exception as exc:
        raise HTTPException(502, f"Unable to load Plex servers: {exc}") from exc
    # The server token is sensitive; keep it server-side and identify the resource by machine id.
    return [
        {
            "name": item["name"],
            "client_identifier": item["client_identifier"],
            "owned": item["owned"],
            "connections": item["connections"],
        }
        for item in resources
    ]


@router.post("/api/setup/plex/server")
def choose_plex_server(payload: ServerPayload, db: Session = Depends(require_setup)) -> dict:
    resources = PlexAccountService(db).resources()
    resource = next(
        (x for x in resources if x["client_identifier"] == payload.client_identifier),
        None,
    )
    if not resource:
        raise HTTPException(404, "Plex server is no longer available")
    connection = next(
        (x for x in resource["connections"] if x["uri"].rstrip("/") == payload.connection_uri.rstrip("/")),
        None,
    )
    if not connection:
        raise HTTPException(400, "Choose a connection reported by Plex")
    token = str(resource.get("access_token") or "")
    if not token:
        raise HTTPException(409, "Plex did not return a server access token")
    candidate = PlexService(payload.connection_uri.rstrip("/"), token)
    info = candidate.connect()
    if not info.connected:
        raise HTTPException(400, "Plumbus cannot reach that Plex server connection")
    row = db.get(PlexServer, 1)
    if row is None:
        row = PlexServer(id=1, base_url=candidate.base_url, token_ciphertext=encrypt_secret(token))
        db.add(row)
    row.base_url = candidate.base_url
    row.token_ciphertext = encrypt_secret(token)
    row.server_name = info.name or resource.get("name")
    row.server_identifier = info.machine_identifier or payload.client_identifier
    row.server_version = info.version
    row.enabled = True
    db.commit()
    return {
        "connected": True,
        "name": row.server_name,
        "version": row.server_version,
        "connection": row.base_url,
    }


@router.get("/api/setup/plex/libraries")
def setup_libraries(db: Session = Depends(require_setup)) -> list[dict]:
    info = PlexService.from_db(db).connect()
    if not info.connected:
        raise HTTPException(409, "Choose a reachable Plex server first")
    existing = {x.plex_key: x for x in db.scalars(select(PlexLibrary)).all()}
    output = []
    for remote in info.libraries or []:
        row = existing.get(str(remote["key"]))
        output.append(
            {
                "key": str(remote["key"]),
                "title": remote["title"],
                "type": remote["type"],
                "enabled": bool(row and row.enabled),
            }
        )
    return output


@router.put("/api/setup/plex/libraries")
def choose_libraries(payload: LibrariesPayload, db: Session = Depends(require_setup)) -> dict:
    server = db.get(PlexServer, 1)
    if not server:
        raise HTTPException(409, "Choose a Plex server first")
    info = PlexService.from_db(db).connect()
    remotes = {str(x["key"]): x for x in (info.libraries or [])}
    selected = set(payload.enabled_keys)
    if not selected:
        raise HTTPException(400, "Enable at least one Plex library")
    for key, remote in remotes.items():
        row = db.scalar(
            select(PlexLibrary).where(PlexLibrary.server_id == server.id, PlexLibrary.plex_key == key)
        )
        if row is None:
            row = PlexLibrary(
                server_id=server.id,
                plex_key=key,
                title=remote["title"],
                library_type=remote["type"],
            )
            db.add(row)
        row.title = remote["title"]
        row.library_type = remote["type"]
        row.enabled = key in selected
        row.visible_to_members = True
    db.commit()
    return {"enabled": sorted(selected)}


@router.put("/api/setup/playback")
def setup_playback(payload: PlaybackPayload, db: Session = Depends(require_setup)) -> dict:
    result = ApplicationSettingsService(db).set_playback(payload.model_dump(), updated_by_id=0)
    # setup has no user yet; avoid a non-existent FK value.
    row = db.get(ApplicationSetting, "playback")
    if row:
        row.updated_by_id = None
    db.commit()
    return result


@router.get("/api/setup/readiness")
def setup_readiness(db: Session = Depends(require_setup)) -> dict:
    return _readiness(db)


@router.post("/api/setup/complete")
def complete_setup(response: Response, db: Session = Depends(require_setup)) -> dict:
    readiness = _readiness(db)
    if not readiness["ready"]:
        raise HTTPException(409, {"message": "Setup is incomplete", **readiness})
    row = db.get(ApplicationSetting, "setup_state")
    if row is None:
        row = ApplicationSetting(key="setup_state", value={})
        db.add(row)
    row.value = {"completed": True}
    claim = db.get(ApplicationSetting, "setup_claim")
    if claim:
        db.delete(claim)
    db.commit()
    response.delete_cookie(SETUP_COOKIE, path="/")
    return {"completed": True, "next": "/api/auth/discord/bootstrap"}


SETUP_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Plumbus Setup</title><style>
:root{font-family:Inter,ui-sans-serif,system-ui;background:#090b10;color:#f4f5f7;color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#20294b55,transparent 36%),#090b10}.wrap{max-width:1080px;margin:auto;padding:36px 22px 80px}.brand{font-weight:900;letter-spacing:.12em}.brand span{color:#8fa8ff}.hero{padding:32px 0}.hero h1{font-size:clamp(38px,6vw,72px);letter-spacing:-.055em;line-height:.95;margin:12px 0}.muted{color:#9ca4b5}.steps{display:grid;grid-template-columns:220px 1fr;gap:22px}.nav,.card{background:#11151d;border:1px solid #232a38;border-radius:18px}.nav{padding:12px;height:max-content;position:sticky;top:20px}.nav button{width:100%;text-align:left;background:none;color:#aab2c2;border:0;padding:12px;border-radius:10px;cursor:pointer}.nav button.active{background:#20283a;color:white}.card{padding:24px;min-height:440px}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}.field{margin:14px 0}.field label{display:block;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:#8d97aa;margin-bottom:7px}input,select{width:100%;padding:12px 13px;border:1px solid #313949;border-radius:10px;background:#0b0e14;color:white}.btn{display:inline-flex;align-items:center;justify-content:center;padding:11px 15px;border-radius:10px;border:1px solid #364156;background:#161c27;color:white;font-weight:750;cursor:pointer}.primary{background:#7691ff;border-color:#7691ff;color:#071020}.good{color:#74d89d}.bad{color:#ff8080}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}.server,.lib{padding:12px;border:1px solid #2b3342;border-radius:12px;margin:8px 0}.code{font-family:ui-monospace,monospace;background:#080a0e;padding:10px;border-radius:9px;word-break:break-all}@media(max-width:760px){.steps{grid-template-columns:1fr}.nav{position:static}.row{grid-template-columns:1fr}}
</style></head><body><div class="wrap"><div class="brand">PLUMBUS <span>// FIRST RUN</span></div><div class="hero"><h1>Set up your private cinema.</h1><p class="muted">No Plex token hunting. Link Plex, choose your server, choose libraries, configure Discord, then create the first SuperAdmin.</p></div><div class="steps"><div class="nav" id="nav"></div><div class="card" id="card"></div></div></div>
<script>
const steps=['Claim setup','Site','Discord','Plex sign-in','Plex server','Libraries','Playback','Finish'];let step=0,claimed=false,pin=null,servers=[],libs=[];
const $=s=>document.querySelector(s);async function api(path,init={}){const r=await fetch(path,{credentials:'include',headers:{'Content-Type':'application/json'},...init});let b={};try{b=await r.json()}catch{}if(!r.ok)throw new Error(typeof b.detail==='string'?b.detail:JSON.stringify(b.detail||b));return b}
function nav(){ $('#nav').innerHTML=steps.map((s,i)=>`<button class="${i===step?'active':''}" onclick="go(${i})">${i+1}. ${s}</button>`).join('') } function go(i){if(!claimed&&i>0)return;step=i;nav();render()}
function buttons(prev=true,next=true){return `<div class="actions">${prev?`<button class="btn" onclick="go(${Math.max(0,step-1)})">Back</button>`:''}${next?`<button class="btn primary" onclick="go(${Math.min(steps.length-1,step+1)})">Continue</button>`:''}</div>`}
async function render(){nav();const c=$('#card');if(step===0)c.innerHTML=`<h2>Claim this installation</h2><p class="muted">Run <span class="code">docker compose logs backend | grep 'SETUP CODE'</span> and enter the one-time code. This stops somebody who finds the public URL first from taking over setup.</p><div class="field"><label>Setup code</label><input id="claim" autocomplete="one-time-code"></div><div class="actions"><button class="btn primary" onclick="claim()">Claim setup</button></div>`;
if(step===1)c.innerHTML=`<h2>Site identity</h2><p class="muted">Use the public HTTPS URL people will use. Discord's callback URL is generated from this automatically.</p><div class="field"><label>Public URL</label><input id="appurl" value="${location.origin}"></div><div class="field"><label>Site name</label><input id="sitename" value="Plumbus Cinema"></div><div class="actions"><button class="btn primary" onclick="site()">Save & continue</button></div>`;
if(step===2){let cfg=await api('/api/setup/config');c.innerHTML=`<h2>Discord authentication</h2><p class="muted">Create a Discord application, open OAuth2, and add this exact redirect URI:</p><div class="code">${cfg.discord.redirect_uri}</div><div class="field"><label>Discord Application / Client ID</label><input id="dcid" value="${cfg.discord.client_id||''}"></div><div class="field"><label>Discord Client Secret ${cfg.discord.client_secret_configured?'(leave blank to keep current)':''}</label><input id="dcsecret" type="password"></div><div class="field"><label>Your Discord user ID (first SuperAdmin)</label><input id="owner" value="${cfg.discord.owner_discord_id||''}"></div><div class="actions"><button class="btn" onclick="go(1)">Back</button><button class="btn primary" onclick="discord()">Save & continue</button></div>`}
if(step===3)c.innerHTML=`<h2>Sign in with Plex</h2><p class="muted">Plumbus uses Plex's official PIN authentication flow. Your password never passes through Plumbus.</p><div id="plexstate"></div><div class="actions"><button class="btn" onclick="go(2)">Back</button><button class="btn primary" onclick="plexStart()">Sign in with Plex</button></div>`;
if(step===4){servers=await api('/api/setup/plex/servers');c.innerHTML=`<h2>Choose Plex Media Server</h2><p class="muted">Local, non-Relay connections are shown first. Pick a reachable connection for the Plumbus server.</p><div>${servers.map(s=>`<div class="server"><strong>${s.name}</strong> ${s.owned?'<span class="good">Owned</span>':''}<div>${s.connections.map(x=>`<button class="btn" style="margin:7px 7px 0 0" onclick='pickServer(${JSON.stringify(s.client_identifier)},${JSON.stringify(x.uri)})'>${x.local?'Local ':''}${x.relay?'Relay ':''}${x.protocol||''} — ${x.uri}</button>`).join('')}</div></div>`).join('')||'<p class="bad">No Plex servers were returned.</p>'}</div>${buttons()}`}
if(step===5){libs=await api('/api/setup/plex/libraries');c.innerHTML=`<h2>Choose libraries</h2><p class="muted">Only enabled movie libraries are indexed and shown to members.</p>${libs.map(x=>`<label class="lib"><input style="width:auto" type="checkbox" data-key="${x.key}" ${x.enabled?'checked':''}> <strong>${x.title}</strong> <span class="muted">${x.type}</span></label>`).join('')}<div class="actions"><button class="btn" onclick="go(4)">Back</button><button class="btn primary" onclick="saveLibs()">Save & continue</button></div>`}
if(step===6){let cfg=await api('/api/setup/config'),p=cfg.playback;c.innerHTML=`<h2>Playback defaults</h2><div class="row"><div class="field"><label>Preferred codec</label><select id="codec"><option ${p.preferred_video_codec==='h264'?'selected':''}>h264</option><option ${p.preferred_video_codec==='hevc'?'selected':''}>hevc</option></select></div><div class="field"><label>Preferred resolution</label><select id="res"><option>720p</option><option ${p.preferred_resolution==='1080p'?'selected':''}>1080p</option><option>1440p</option><option ${['4k','2160p'].includes(String(p.preferred_resolution).toLowerCase())?'selected':''}>4k</option></select></div></div><div class="field"><label>Max bitrate (Kbps)</label><input id="br" type="number" value="${p.max_stream_bitrate_kbps}"></div><label class="lib"><input style="width:auto" id="trans" type="checkbox" ${p.allow_plex_transcoding?'checked':''}> Allow Plex transcoding when direct play is unsuitable</label><div class="actions"><button class="btn" onclick="go(5)">Back</button><button class="btn primary" onclick="playback()">Save & continue</button></div>`}
if(step===7){let r=await api('/api/setup/readiness');c.innerHTML=`<h2>Ready check</h2>${Object.entries(r.checks).map(([k,v])=>`<div class="lib"><span class="${v?'good':'bad'}">${v?'✓':'✕'}</span> ${k.replaceAll('_',' ')}</div>`).join('')}<p class="muted">Finishing locks the unauthenticated setup wizard. You will then sign into Discord as the configured first SuperAdmin.</p><div class="actions"><button class="btn" onclick="go(6)">Back</button><button class="btn primary" ${r.ready?'':'disabled'} onclick="finish()">Finish setup & sign in</button></div>`}}
async function claim(){try{await api('/api/setup/claim',{method:'POST',body:JSON.stringify({code:$('#claim').value})});claimed=true;go(1)}catch(e){alert(e.message)}}
async function site(){try{await api('/api/setup/site',{method:'PUT',body:JSON.stringify({app_url:$('#appurl').value,site_name:$('#sitename').value})});go(2)}catch(e){alert(e.message)}}
async function discord(){try{await api('/api/setup/discord',{method:'PUT',body:JSON.stringify({client_id:$('#dcid').value,client_secret:$('#dcsecret').value,owner_discord_id:$('#owner').value})});go(3)}catch(e){alert(e.message)}}
async function plexStart(){try{let r=await api('/api/setup/plex/sign-in',{method:'POST'});pin=r.pin_id;window.open(r.auth_url,'_blank','noopener');$('#plexstate').innerHTML='<p class="muted">Waiting for Plex sign-in…</p>';poll()}catch(e){alert(e.message)}}
async function poll(){if(!pin)return;try{let r=await api('/api/setup/plex/sign-in/'+pin);if(r.authenticated){$('#plexstate').innerHTML='<p class="good">✓ Plex account linked.</p>';setTimeout(()=>go(4),600);return}}catch(e){}setTimeout(poll,1800)}
async function pickServer(id,uri){try{await api('/api/setup/plex/server',{method:'POST',body:JSON.stringify({client_identifier:id,connection_uri:uri})});go(5)}catch(e){alert(e.message)}}
async function saveLibs(){try{let keys=[...document.querySelectorAll('[data-key]:checked')].map(x=>x.dataset.key);await api('/api/setup/plex/libraries',{method:'PUT',body:JSON.stringify({enabled_keys:keys})});go(6)}catch(e){alert(e.message)}}
async function playback(){try{await api('/api/setup/playback',{method:'PUT',body:JSON.stringify({preferred_video_codec:$('#codec').value,preferred_resolution:$('#res').value,max_stream_bitrate_kbps:Number($('#br').value),allow_plex_transcoding:$('#trans').checked})});go(7)}catch(e){alert(e.message)}}
async function finish(){try{let r=await api('/api/setup/complete',{method:'POST'});location.href=r.next}catch(e){alert(e.message)}}
(async()=>{let s=await api('/api/setup/status');if(s.completed){location.href='/';return}claimed=s.claimed;if(claimed)step=1;render()})()
</script></body></html>'''
