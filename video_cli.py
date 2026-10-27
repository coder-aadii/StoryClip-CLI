#!/usr/bin/env python3
"""Standalone terminal-only photo-video generator; explicit music-rights approval required."""
import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'storyclip-cli/0.1'})


def log(s):
    print(f'[storyclip] {s}', flush=True)


def run(cmd):
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"Command failed: {' '.join(str(x) for x in cmd[:8])}\n{p.stderr[-3000:]}")
    return p.stdout.strip()


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def check_tools():
    for name in ('ffmpeg', 'ffprobe'):
        if not shutil.which(name):
            raise RuntimeError(f'{name} not found. Install with: sudo apt install ffmpeg')


def audio_duration(path):
    return float(json.loads(run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',path]))['format']['duration'])


def gemini_json(prompt):
    from google import genai
    from google.genai import types
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    model=os.getenv('GEMINI_TEXT_MODEL','gemini-2.5-flash')
    last=None
    for attempt in range(3):
        try:
            response=client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(response_mime_type='application/json',temperature=0.6))
            return json.loads(response.text)
        except Exception as e:
            last=e
            if attempt<2: time.sleep(2**attempt)
    raise RuntimeError(f'Gemini text generation failed: {last}')


def narrative_from_idea(idea, language, minutes):
    log('Generating narration script with Gemini')
    words=int(minutes*(145 if language=='hi' else 155))
    prompt=(f'Create an original, engaging spoken narration for a photo-based documentary/story video. '
            f'Language: {"Hindi in Devanagari" if language=="hi" else "English"}. Idea: {idea}. '
            f'Approximate spoken length: {minutes} minutes (~{words} words, prioritize natural pacing). '
            'Write a complete plot with compelling sensory detail; satisfy the requested length without repetition. No time codes, no camera directions, no headings, no background-music directions in the narration. '
            'Do not invent factual claims presented as established truths; when fictional, make it clearly fiction. '
            'Return JSON object with keys "title" (short text), "narration" (continuous spoken text), '
            '"theme" (2-4 English words describing photo background), "music_moods" (array of 2 English keywords).')
    result=gemini_json(prompt)
    if not isinstance(result.get('narration'),str) or len(result['narration'])<30: raise ValueError('Gemini returned empty narration')
    return result


def classify_mood(text):
    value = text.lower()
    if any(word in value for word in ('horror', 'haunt', 'ghost', 'creep', 'fear', 'scary', 'भूत', 'डर', 'भयानक', 'रहस्यमयी', 'चुड़ैल')):
        return 'horror'
    if any(word in value for word in ('joy', 'happy', 'party', 'festival', 'celebrat', 'खुशी', 'उत्सव', 'जश्न')):
        return 'joy'
    return 'neutral'


def expand_narration(project, seconds_missing):
    """Append a story continuation; never time-stretch narration to meet requested minutes."""
    language = project['language']
    extra_words = max(70, int(seconds_missing * (2.3 if language == 'hi' else 2.55)))
    prompt = (f'Continue this original narrative naturally in {"Hindi (Devanagari)" if language == "hi" else "English"}. '
              f'Write approximately {extra_words} ADDITIONAL spoken words, adding meaningful action or resolution, not repetition. '
              'Do not repeat the ending; this continuation will be appended after the existing text. '
              'Return JSON with one key: "continuation". Story so far:\n' + project['narration'][-7500:])
    result = gemini_json(prompt)
    addition = str(result.get('continuation', '')).strip()
    if len(addition) < 70:
        raise RuntimeError('Gemini returned an empty/short continuation')
    return addition


def sentences(text):
    # Hindi danda and sentence-final English punctuation; do not split on abbreviations perfectly.
    chunks=re.split(r'(?<=[.!?।！？])\s+|(?<=।)(?=[^\s])',re.sub(r'\s+',' ',text.strip()))
    return [s.strip() for s in chunks if s.strip()]


def plan_scenes(narration, language, theme):
    log('Planning scene imagery using Gemini')
    units=sentences(narration)
    # group short sentences into approximate 10s segments; long sentences remain intact.
    wps=1.75 if language=='hi' else 2.25
    groups=[]; buf=[]; secs=0
    for s in units:
        est=max(1.5,len(s.split())/wps)
        if buf and (secs+est>12.5 or secs>=7.5):
            groups.append(' '.join(buf));buf=[];secs=0
        buf.append(s);secs+=est
    if buf:groups.append(' '.join(buf))
    query_text='\n'.join(f'{i}: {g[:420]}' for i,g in enumerate(groups))
    prompt=(f'For every numbered narration segment, suggest one visually relevant Pexels PHOTO search query. '
            f'Narration language {language}; theme: {theme}. Queries MUST be ENGLISH, photographic, concrete, '
            'not abstract moods. 2-6 keywords. Keep visual descriptions faithful to what narrator says; '
            'do not hallucinate or claim that a visual is an exact depiction of real people/events. '
            'Return JSON with key "queries", array of objects {"id":integer,"query":string} covering all ids. '
            'Segments:\n'+query_text)
    # Chunk so long videos do not overload the model.
    mapping={}
    for offset in range(0,len(groups),35):
        subset=groups[offset:offset+35]
        local='\n'.join(f'{offset+i}: {g[:420]}' for i,g in enumerate(subset))
        data=gemini_json(prompt.split('Segments:\n')[0]+'Segments:\n'+local)
        for x in data.get('queries',[]):
            if isinstance(x,dict) and str(x.get('id','')).isdigit(): mapping[int(x['id'])]=str(x.get('query',''))
    return [{'text':s,'query':mapping.get(i,theme),'image':None,'mood':classify_mood(s+' '+theme)} for i,s in enumerate(groups)]


def pexels_search(query, count=8):
    r=SESSION.get('https://api.pexels.com/v1/search',headers={'Authorization':os.environ['PEXELS_API_KEY']},params={'query':query,'per_page':count,'orientation':'landscape'},timeout=25)
    r.raise_for_status()
    return r.json().get('photos',[])


def fetch_file(url, path, max_bytes=25_000_000):
    if not url.startswith('https://'): raise ValueError('HTTPS download URL required')
    # Do not follow arbitrary user-supplied URLs; only provider-returned image/audio URLs.
    with SESSION.get(url,stream=True,timeout=45) as r:
        r.raise_for_status(); size=0
        with path.open('wb') as f:
            for chunk in r.iter_content(128*1024):
                size+=len(chunk)
                if size>max_bytes: raise RuntimeError('Download exceeded size limit')
                f.write(chunk)
    if size<512: raise RuntimeError('Downloaded file unexpectedly small')


def gather_images(scenes, theme, folder):
    folder.mkdir(exist_ok=True,parents=True)
    fallbacks=[]
    log('Downloading themed fallback photos')
    for p in pexels_search(theme,12):
        url=p.get('src',{}).get('large2x') or p.get('src',{}).get('large')
        if url and len(fallbacks)<8:
            try:
                dest=folder/f'fallback_{p["id"]}.jpg'
                if not dest.exists():fetch_file(url,dest)
                fallbacks.append({'path':str(dest),'credit':p.get('photographer'),'pexels_url':p.get('url')})
            except Exception as ex: log(f'Fallback photo failed: {ex}')
    if not fallbacks: raise RuntimeError('Pexels provided no downloadable theme fallback photos')
    used=set()
    for i,sc in enumerate(scenes):
        dest=folder/f'scene_{i:04}.jpg'
        if dest.exists(): sc['image']=str(dest);continue
        photo=None
        try:
            matches=pexels_search(sc['query'],8)
            for p in matches:
                if p['id'] not in used:
                    url=p.get('src',{}).get('large2x') or p.get('src',{}).get('large')
                    if url:
                        try: fetch_file(url,dest); photo=p; used.add(p['id']);break
                        except Exception:pass
        except Exception as ex: log(f'Pexels search failed for scene {i}: {ex}')
        if photo:
            sc['image']=str(dest);sc['credit']=photo.get('photographer');sc['pexels_url']=photo.get('url')
        else:
            fb=fallbacks[i%len(fallbacks)]
            shutil.copyfile(fb['path'],dest)
            sc['image']=str(dest);sc['credit']=fb['credit'];sc['pexels_url']=fb['pexels_url'];sc['fallback']=True
    return scenes


async def edge_speak(text, dest, language, mood='neutral'):
    # Edge offers voice/rate control but not reliable acting/emotion instructions.
    import edge_tts
    voice='hi-IN-SwaraNeural' if language=='hi' else 'en-US-AriaNeural'
    await edge_tts.Communicate(text,voice,rate=('-12%' if mood=='horror' else '+0%' if mood=='joy' else '-5%')).save(str(dest))


def gemini_speak(text, dest, language, mood='neutral'):
    # Gemini generateContent single-speaker API. SDK response may be WAV or raw PCM.
    from google import genai
    from google.genai import types
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    delivery = {'horror': 'Narrate with restrained dread, uneasy pauses and rising tension. Sound like a human telling a frightening story, not an announcer.', 'joy': 'Narrate warmly, smiling, animated and full of genuine delight.', 'neutral': 'Narrate naturally with authentic storytelling emotion and varied intonation.'}.get(mood,'Narrate naturally.')
    response=client.models.generate_content(
        model=os.getenv('GEMINI_TTS_MODEL','gemini-2.5-flash-preview-tts'),
        contents=f'{delivery} Speak the following words verbatim in {"Hindi" if language=="hi" else "English"}; do not read the directions aloud. Script: {text}',
        config=types.GenerateContentConfig(response_modalities=['AUDIO'],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Kore')))))
    parts=response.candidates[0].content.parts
    blob=next((p.inline_data for p in parts if getattr(p,'inline_data',None)),None)
    if not blob or not blob.data:raise RuntimeError('Gemini returned no audio')
    data=blob.data
    if data[:4]==b'RIFF':dest.write_bytes(data)
    else:
        with wave.open(str(dest),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(data)


def narrate(scenes, folder, language, provider):
    folder.mkdir(parents=True,exist_ok=True)
    durations=[]
    for i,sc in enumerate(scenes):
        wav=folder/f'{i:04}.wav'
        if not wav.exists():
            raw=folder/f'{i:04}.source'
            log(f'TTS scene {i+1}/{len(scenes)}')
            if provider in ('gemini','auto'):
                try:
                    gemini_speak(sc['text'],wav,language,sc.get('mood','neutral'))
                except Exception as ex:
                    if provider=='gemini':raise
                    log(f'Gemini TTS unavailable, using Edge: {ex}')
            if not wav.exists():
                mp3=folder/f'{i:04}.mp3'
                asyncio.run(edge_speak(sc['text'],mp3,language,sc.get('mood','neutral')))
                run(['ffmpeg','-y','-loglevel','error','-i',mp3,'-ar','24000','-ac','1',wav])
        durations.append(audio_duration(wav))
        sc['voice']=str(wav)
        sc['duration']=durations[-1]
    # concat PCM WAV files via ffmpeg concat demuxer
    manifest=folder/'narration_concat.txt'
    manifest.write_text(''.join("file '"+Path(sc['voice']).name+"'\n" for sc in scenes),encoding='utf-8')
    output=folder/'narration.wav'
    run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',manifest,'-ar','24000','-ac','1',output])
    return output


def make_srt(scenes, output):
    def stamp(s):
        ms=int(round(s*1000));hr,ms=divmod(ms,3600000);mn,ms=divmod(ms,60000);sec,ms=divmod(ms,1000)
        return f'{hr:02}:{mn:02}:{sec:02},{ms:03}'
    curr=0;records=[]
    for i,sc in enumerate(scenes,1):
        end=curr+sc['duration']
        records.append(f'{i}\n{stamp(curr)} --> {stamp(end)}\n{sc["text"]}\n')
        sc['start']=curr;sc['end']=end
        curr=end
    output.write_text('\n'.join(records)+'\n',encoding='utf-8')
    return curr


def jamendo_candidates(mood,limit=12):
    r=SESSION.get('https://api.jamendo.com/v3.0/tracks/',params={
        'client_id':os.environ['JAMENDO_CLIENT_ID'],'format':'json','limit':limit,
        'search':mood,'vocalinstrumental':'instrumental','include':'licenses',
    },timeout=25)
    r.raise_for_status();data=r.json()
    if data.get('headers',{}).get('status')!='success':
        raise RuntimeError(data.get('headers',{}).get('error_message','Jamendo search failed'))
    return data.get('results',[])


def select_bgm(moods, folder, duration, approved):
    """Choose one or more tracks but require explicit proof of additional rights.
    A Creative Commons NC/ND track must NEVER be silently used in monetized video.
    """
    folder.mkdir(exist_ok=True,parents=True)
    tracks=[]; seen=set()
    for mood in moods[:3]:
        for t in jamendo_candidates(mood):
            tid=t.get('id')
            if tid not in seen and t.get('audiodownload_allowed') and t.get('audiodownload'):
                tracks.append(t);seen.add(tid)
    if not tracks:
        log('No downloadable Jamendo tracks returned; video will have narration only.')
        return None,[]
    log('Jamendo candidates (licenses are NOT automatically cleared for synchronization/commercial use):')
    for i,t in enumerate(tracks[:10]):
        log(f'{i+1}. {t.get("name")} by {t.get("artist_name")} | {t.get("license_ccurl")} | {t.get("duration")} s')
    if not approved:
        log('Skipping Jamendo BGM. To import a track, provide --music-rights-confirmed only after independently securing rights.')
        return None, tracks
    if not sys.stdin.isatty():
        raise RuntimeError('Music-rights confirmation requires an interactive terminal.')
    print('You confirm you possess rights to synchronize and, if needed, commercially publish selected tracks.')
    if input('Type I HAVE THE RIGHTS to proceed: ').strip()!='I HAVE THE RIGHTS':
        log('Music not approved; using narration only.');return None,tracks
    chosen=[];total=0
    for t in tracks:
        if total>=duration:break
        url=t.get('audiodownload')
        dest=folder/f'jamendo_{t["id"]}.mp3'
        try:
            if not dest.exists():fetch_file(url,dest,40_000_000)
            length=audio_duration(dest)
            if length<8:continue
            chosen.append({'path':str(dest),'id':t['id'],'name':t.get('name'),'artist':t.get('artist_name'), 'license':t.get('license_ccurl')})
            total+=length
        except Exception as e: log(f'Music track download failed: {e}')
    save_json(folder/'credits.json',chosen)
    return chosen,tracks


def render(scenes, narration, music, output_dir, fps=24, width=1280, height=720, style='auto', zoom=True, bgm_volume=0.12):
    """Render a lightweight Ken Burns photo movie with theme grading and fades."""
    clips = output_dir / 'clips'
    clips.mkdir(exist_ok=True)
    dur = [float(x['duration']) for x in scenes]
    fade = min(0.45, min(dur)/4) if len(dur) > 1 else 0
    theme = style if style != 'auto' else classify_mood(' '.join(x.get('mood','') for x in scenes))
    for i, sc in enumerate(scenes):
        dest = clips / f'{i:04}.mp4'
        seconds = dur[i] + (fade if i < len(scenes)-1 else 0)
        frames = max(1, round(seconds*fps))
        # Zoompan produces frames from one high-resolution still; alternating directions.
        if zoom:
            factor = 'min(zoom+0.00045,1.12)' if i % 2 == 0 else 'max(1.12-on*0.00045,1.0)'
            vf = (f'scale={int(width*1.3)}:{int(height*1.3)}:force_original_aspect_ratio=increase,'
                  f'crop={int(width*1.3)}:{int(height*1.3)},'
                  f'zoompan=z=\'{factor}\':x=\'iw/2-iw/zoom/2\':y=\'ih/2-ih/zoom/2\':'
                  f'd={frames}:s={width}x{height}:fps={fps}')
        else:
            vf = f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}'
        if theme == 'horror':
            vf += ',eq=brightness=-0.035:saturation=0.80,vignette=PI/4'
        elif theme == 'joy':
            vf += ',eq=brightness=0.018:saturation=1.23:contrast=1.04'
        vf += ',setsar=1,format=yuv420p'
        # Fingerprint inputs: rerenders on effects changes but preserves other usable assets.
        signature = hashlib.sha256(json.dumps([str(sc['image']),os.path.getmtime(sc['image']),seconds,fps,width,height,vf],sort_keys=True).encode()).hexdigest()
        stamp = dest.with_suffix('.sha256')
        if dest.exists() and stamp.exists() and stamp.read_text() == signature:
            continue
        log(f'Rendering motion image {i+1}/{len(scenes)}')
        run(['ffmpeg','-y','-loglevel','error','-loop','1','-framerate',str(fps),'-i',sc['image'],
             '-vf',vf,'-frames:v',str(frames),'-c:v','libx264','-preset','veryfast','-crf','23',
             '-pix_fmt','yuv420p',dest])
        stamp.write_text(signature)
    visual = output_dir / 'visual_only.mp4'
    files = [clips / f'{i:04}.mp4' for i in range(len(scenes))]
    if len(files) == 1:
        shutil.copyfile(files[0],visual)
    else:
        inputs = []
        for f in files: inputs.extend(['-i',f])
        parts = []
        label = '0:v'
        elapsed = dur[0]
        for i in range(1,len(files)):
            nxt = f'v{i}'
            parts.append(f'[{label}][{i}:v]xfade=transition=fade:duration={fade:.3f}:offset={elapsed:.3f}[{nxt}]')
            label = nxt
            elapsed += dur[i]
        run(['ffmpeg','-y','-loglevel','error',*inputs,'-filter_complex',';'.join(parts),
             '-map',f'[{label}]','-c:v','libx264','-preset','veryfast','-crf','23',
             '-pix_fmt','yuv420p',visual])
    out = output_dir / 'final.mp4'
    total = sum(dur)
    if music:
        # Loop a selected rights-cleared track. Multiple-track playlists need a separate
        # sequential normalisation step; do not misuse -stream_loop on concat demuxer.
        first = music[0]['path']
        filtergraph = (f'[2:a]volume={bgm_volume},afade=t=in:st=0:d=1.5,'
                       f'afade=t=out:st={max(0,total-2):.3f}:d=2[bg];'
                       '[1:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]')
        args = ['ffmpeg','-y','-loglevel','error','-i',visual,'-i',narration,
                '-stream_loop','-1','-i',first,'-filter_complex',filtergraph,
                '-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k',
                '-t',f'{total:.3f}','-movflags','+faststart',out]
    else:
        args = ['ffmpeg','-y','-loglevel','error','-i',visual,'-i',narration,
                '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k',
                '-t',f'{total:.3f}','-movflags','+faststart',out]
    run(args)
    return out


def main():
    p=argparse.ArgumentParser(description='Idea/script -> Pexels photo video, TTS, optional Jamendo BGM, SRT')
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--idea',help='One-line idea')
    source.add_argument('--script',type=Path,help='Existing UTF-8 script file')
    p.add_argument('--language',choices=['hi','en'],default='hi')
    p.add_argument('--minutes',type=float,default=2,help='Approximate length for idea-to-script')
    p.add_argument('--tts',choices=['edge','gemini','auto'],default='edge')
    p.add_argument('--output',type=Path,default=Path('outputs/demo'))
    p.add_argument('--theme',help='English Pexels theme search override')
    p.add_argument('--music-mood',help='Comma-separated Jamendo search moods')
    p.add_argument('--music-rights-confirmed',action='store_true',help='Require interactive affirmation of separately cleared synchronization/commercial rights')
    p.add_argument('--skip-music',action='store_true')
    p.add_argument('--dry-run',action='store_true',help='Stop after script, scenes and search queries are planned')
    p.add_argument('--fps',type=int,default=24)
    p.add_argument('--style',choices=['auto','horror','joy','neutral'],default='auto')
    p.add_argument('--no-zoom',action='store_true')
    p.add_argument('--bgm-file',type=Path,help='Local music file for which you have suitable video-sync rights')
    p.add_argument('--bgm-volume',type=float,default=0.12)
    p.add_argument('--duration-tolerance',type=float,default=0.07,help='Fractional tolerance for idea-generated videos')
    args=p.parse_args()
    if args.minutes<=0 or args.minutes>60: p.error('--minutes must be between 0 and 60')
    check_tools()
    if not 0 <= args.bgm_volume <= 1: p.error('--bgm-volume must be between 0 and 1')
    for key in ('GEMINI_API_KEY',):
        if not os.getenv(key):p.error(f'Missing {key} in .env')
    if not args.dry_run and not os.getenv('PEXELS_API_KEY'):p.error('Missing PEXELS_API_KEY in .env')
    args.output.mkdir(parents=True,exist_ok=True)
    meta=args.output/'project.json'
    if meta.exists():
        project=json.loads(meta.read_text(encoding='utf-8'))
        log('Resuming saved project manifest')
    else:
        if args.idea:project=narrative_from_idea(args.idea,args.language,args.minutes)
        else:
            content=args.script.read_text(encoding='utf-8').strip()
            project={'title':args.script.stem,'narration':content,'theme':args.theme or 'cinematic nature landscape','music_moods':['cinematic']}
        project['language']=args.language
        project['theme']=args.theme or project.get('theme') or 'cinematic landscape'
        project['music_moods']=[x.strip() for x in args.music_mood.split(',')] if args.music_mood else project.get('music_moods',['cinematic'])
        project['scenes']=plan_scenes(project['narration'],args.language,project['theme'])
        save_json(meta,project)
        (args.output/'script.txt').write_text(project['narration'],encoding='utf-8')
    if args.dry_run:
        log(f'Dry run: {len(project["scenes"])} planned scenes in {meta}');return
    scenes=project['scenes']
    gather_images(scenes,project['theme'],args.output/'images');save_json(meta,project)
    voice=narrate(scenes,args.output/'voice',project['language'],args.tts)
    length=make_srt(scenes,args.output/'subtitles.srt');save_json(meta,project)
    if args.idea and project.get('narration') and length < 60*args.minutes*(1-args.duration_tolerance):
        # At most two continuations, avoiding endless retries and preserving old audio.
        for attempt in range(2):
            deficit=60*args.minutes-length
            log(f'Script shorter than target by {deficit:.1f}s; requesting meaningful continuation {attempt+1}/2')
            old=len(scenes)
            extra=expand_narration(project,deficit)
            added=plan_scenes(extra,project['language'],project['theme'])
            scenes.extend(added)
            project['narration'] += '\n' + extra
            # Scene files are indexed; existing assets/narration remain reusable.
            save_json(meta,project)
            (args.output/'script.txt').write_text(project['narration'],encoding='utf-8')
            gather_images(scenes,project['theme'],args.output/'images')
            voice=narrate(scenes,args.output/'voice',project['language'],args.tts)
            length=make_srt(scenes,args.output/'subtitles.srt')
            save_json(meta,project)
            if length >= 60*args.minutes*(1-args.duration_tolerance): break
        if abs(length-60*args.minutes)>60*args.minutes*args.duration_tolerance:
            log(f'WARNING: Actual {length:.1f}s is outside requested duration tolerance; no artificial slowdown applied.')
    log(f'Narration duration: {length:.1f}s; scenes: {len(scenes)}')
    music=None
    if args.bgm_file:
        if not args.bgm_file.is_file(): p.error('--bgm-file does not exist')
        music=[{'path':str(args.bgm_file.resolve())}]
    elif not args.skip_music:
        if os.getenv('JAMENDO_CLIENT_ID'):
            try:music,candidates=select_bgm(project['music_moods'],args.output/'music',length,args.music_rights_confirmed)
            except Exception as ex:log(f'Jamendo unavailable: {ex}; rendering narration-only')
        else:log('No JAMENDO_CLIENT_ID; rendering narration-only')
    movie=render(scenes,voice,music,args.output,args.fps,style=args.style,zoom=not args.no_zoom,bgm_volume=args.bgm_volume)
    log(f'VIDEO: {movie}\nSRT: {args.output / "subtitles.srt"}\nMANIFEST: {meta}')


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,Exception) as ex:
        log(f'ERROR: {ex}');sys.exit(1)
