"""Local download jobs backed by the official yt-dlp executable."""
import json,os,re,secrets,subprocess,threading
from pathlib import Path
from urllib.parse import urlparse
from process_guard import attach_to_server, run_managed
from subtitles import guess_search

NO_WINDOW=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
_INVALID_WIN_CHARS=re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WIN_NAMES={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}

def _safe_name(text,fallback='Video',max_len=100):
    cleaned=_INVALID_WIN_CHARS.sub(' ',str(text or ''))
    cleaned=re.sub(r'\s+',' ',cleaned).strip().rstrip('. ')
    if not cleaned or cleaned in ('.','..') or cleaned.upper() in _RESERVED_WIN_NAMES:
        return fallback
    return cleaned[:max_len].rstrip('. ') or fallback

def validate_link(value):
    if not isinstance(value,str) or len(value)>8192:raise ValueError('Paste a valid HTTP/HTTPS video link.')
    parsed=urlparse(value.strip())
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Paste a valid HTTP/HTTPS video link.')
    return value.strip()

class DownloadService:
    def __init__(self,tool,ffmpeg,destination=None):
        self.tool=str(tool);self.ffmpeg=str(Path(ffmpeg).parent)
        self.destination=Path(destination or Path.home()/'Downloads'/'StreamChromePro')
        self.jobs={};self.lock=threading.RLock()
        self._closing=False
    def command(self):
        runtime=Path(self.tool).with_name('deno.exe')
        runtime_args=['--js-runtimes','deno:'+str(runtime)] if runtime.is_file() else []
        return [self.tool]+runtime_args+['--ignore-config','--no-playlist','--playlist-end','2','--no-warnings','--no-colors','--socket-timeout','20','--retries','2','--no-exec','--ffmpeg-location',self.ffmpeg]
    def analyze(self,url):
        with self.lock:
            if self._closing:raise ValueError('The download service is shutting down.')
        url=validate_link(url)
        try:
            result=run_managed(self.command()+['--check-formats','--skip-download','--dump-single-json','--',url],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=75,creationflags=NO_WINDOW)
        except FileNotFoundError:raise ValueError('The downloader is unavailable. Update the desktop app.') from None
        except subprocess.TimeoutExpired:raise ValueError('Link analysis timed out. Try again.') from None
        if result.returncode:raise ValueError('Could not analyze this link. It may be unavailable, require sign-in, or be unsupported.')
        try:info=json.loads(result.stdout)
        except ValueError:raise ValueError('The website returned an unsupported response.') from None
        if info.get('_type') in ('playlist','multi_video') or 'entries' in info:raise ValueError('Paste a single video link instead of a playlist.')
        if info.get('is_live') or info.get('live_status')=='is_live':raise ValueError('Live streams are not available for download.')
        formats=[f for f in info.get('formats',[]) if not f.get('has_drm') and f.get('vcodec')!='none']
        if not formats and info.get('has_drm'):raise ValueError('Protected videos cannot be downloaded.')
        heights=sorted({int(f['height']) for f in formats if isinstance(f.get('height'),(int,float)) and 0<f['height']<=8640},reverse=True)
        with self.lock:
            if self._closing:raise ValueError('The download service is shutting down.')
            # Do not evict an active job while pruning previous analyses.
            if len(self.jobs)>=12:
                old=next((key for key,item in self.jobs.items() if item['state'] not in ('downloading','processing')),None)
                if old is None:raise ValueError('Finish or cancel the current download first.')
                self.jobs.pop(old)
            token=secrets.token_urlsafe(24)
            self.jobs[token]={'id':token,'url':url,'title':str(info.get('title') or 'Video')[:300],'duration':info.get('duration'),'heights':heights,'state':'ready','downloaded':0,'total':0,'speed':0,'eta':None,'path':None,'process':None,'cancel':False,'error':'','info':info}
            return self.status(token)
    def status(self,token):
        with self.lock:
            job=self.jobs.get(token)
            if job is None:raise ValueError('Download expired. Analyze the link again.')
            return {**{key:job[key] for key in ('id','title','duration','heights','state','downloaded','total','speed','eta','error')},'source':'remote:'+token}
    def playback(self,token):
        with self.lock:
            job=self.jobs.get(token)
            if not job:raise ValueError('Video link expired. Press Play link again.')
            info=job.get('info',{})
            formats=info.get('requested_formats') or [info]
            video=next((f for f in formats if f.get('vcodec') not in (None,'none') and f.get('url')),None)
            if not video:raise ValueError('This link does not provide a playable video stream.')
            audio=next((f for f in formats if f.get('acodec') not in (None,'none') and f.get('url')),None)
            def headers(item):
                values={**info.get('http_headers',{}),**item.get('http_headers',{})}
                return ''.join(f'{key}: {value}\r\n' for key,value in values.items() if key.lower() in ('user-agent','referer','origin') and '\r' not in str(value) and '\n' not in str(value))
            source=validate_link(video['url'])
            extra=audio if audio and audio['url']!=video['url'] else None
            return source,{'headers':headers(video),'audio_url':validate_link(extra['url']) if extra else None,'audio_headers':headers(extra) if extra else '', 'title':job['title']}
    def build_target_folder_and_template(self,job):
        info=job.get('info') or {}
        guessed=guess_search(job.get('title') or '')
        dest=Path(job.get('destination') or self.destination)
        dest_lower=dest.name.lower()
        is_series=bool(info.get('series') or info.get('season_number') or guessed.get('type')=='series' or dest_lower=='series')
        raw_year=str(info.get('release_year') or str(info.get('upload_date') or '')[:4] or guessed.get('year') or '')
        year_m=re.search(r'(19|20)\d{2}',raw_year)
        year=year_m.group(0) if year_m else ''
        raw_title=info.get('series') if is_series and info.get('series') else (guessed.get('title') or job.get('title') or 'Video')
        clean_title=_safe_name(raw_title,fallback='Video',max_len=90)
        folder_name=f'{clean_title} ({year})' if year else clean_title
        base_root=dest.parent if dest_lower in ('movies','filmes','series') else None
        if is_series:
            season=max(1,int(info.get('season_number') or guessed.get('season') or 1))
            episode=max(1,int(info.get('episode_number') or guessed.get('episode') or 1))
            root_cat=(base_root/'Series') if base_root else dest
            folder=(root_cat/_safe_name(folder_name,max_len=100)/f'Season {season:02d}')
            template=f'{clean_title} - S{season:02d}E{episode:02d}.%(ext)s'
        else:
            root_cat=(base_root/'Movies') if base_root else dest
            folder=(root_cat/_safe_name(folder_name,max_len=100))
            template=f'{folder_name}.%(ext)s'
        return folder,template
    def start(self,token,quality):
        with self.lock:
            if self._closing:raise ValueError('The download service is shutting down.')
            job=self.jobs.get(token)
            if job is None:raise ValueError('Analyze the link first.')
            if job['state']!='ready':raise ValueError('Analyze the link again before another download.')
            if any(j['state'] in ('downloading','processing') for j in self.jobs.values()):raise ValueError('Finish or cancel the current download first.')
            if quality!='best' and (not str(quality).isdigit() or int(quality) not in job['heights']):raise ValueError('Choose an available quality.')
            job.update(state='downloading',cancel=False,destination=self.destination)
            threading.Thread(target=self._download,args=(job,quality),daemon=True).start()
            return self.status(token)
    def _download(self,job,quality):
        folder,template=self.build_target_folder_and_template(job);process=None
        try:
            folder.mkdir(parents=True,exist_ok=True)
            height='' if quality=='best' else f'[height<={int(quality)}]'
            selection=f'bv*{height}+ba/b{height}'
            args=self.command()+['--newline','--progress','--progress-template','download:PROGRESS:%(progress)j','--print','after_move:FILE:%(filepath)j','--no-simulate','--windows-filenames','--no-overwrites','--merge-output-format','mkv','-f',selection,'-P',str(folder),'-o',template,'--',job['url']]
            process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=NO_WINDOW)
            attach_to_server(process)
            with self.lock:job['process']=process
            if job['cancel']:self._stop(process)
            for line in process.stdout:
                with self.lock:
                    if line.startswith('PROGRESS:'):
                        try:
                            data=json.loads(line[len('PROGRESS:'):]);job.update(downloaded=data.get('downloaded_bytes') or 0,total=data.get('total_bytes') or data.get('total_bytes_estimate') or 0,speed=data.get('speed') or 0,eta=data.get('eta'))
                            if data.get('status')=='finished':job['state']='processing'
                            elif data.get('status')=='downloading':job['state']='downloading'
                        except (ValueError,TypeError):pass
                    elif line.startswith('FILE:'):
                        candidate=Path(json.loads(line[len('FILE:'):])).resolve()
                        if candidate.is_relative_to(folder.resolve()) and candidate.is_file():job['path']=candidate
            code=process.wait()
            with self.lock:
                if job['cancel']:job['state']='cancelled'
                elif code==0 and job['path']:job['state']='complete'
                else:job.update(state='failed',error='Download failed. Check the link and try again.')
        except (OSError,ValueError):
            with self.lock:job.update(state='cancelled' if job['cancel'] else 'failed',error='' if job['cancel'] else 'Download failed. Check your connection and available disk space.')
        finally:
            if process and process.poll() is None:self._stop(process)
            if process and process.stdout:process.stdout.close()
            with self.lock:job['process']=None
    def _stop(self,process):
        if process.poll() is not None:return
        if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=NO_WINDOW)
        else:process.terminate()
    def shutdown(self):
        with self.lock:
            if self._closing:return
            self._closing=True
            processes=[]
            for job in self.jobs.values():
                if job['state'] in ('downloading','processing'):
                    job['cancel']=True
                    if job.get('process') is not None:processes.append(job['process'])
        for process in processes:
            threading.Thread(target=self._stop,args=(process,),daemon=True).start()
    def cancel(self,token):
        with self.lock:
            job=self.jobs.get(token)
            if not job:raise ValueError('Download expired.')
            if job['state'] not in ('downloading','processing'):return self.status(token)
            job['cancel']=True;process=job['process']
        if process:self._stop(process)
        return self.status(token)
    def completed_file(self,token):
        with self.lock:
            job=self.jobs.get(token)
            if not job or job['state']!='complete' or not job['path'] or not job['path'].is_file():raise ValueError('The download is not complete.')
            return job['path']
