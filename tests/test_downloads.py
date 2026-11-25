import json,sys,tempfile,time,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from downloads import DownloadService,validate_link

class DownloadTests(unittest.TestCase):
 def test_input_and_quality_validation(self):
  for value in ['file:///secret','--exec bad','https://user:password@example.org/a',None]:
   with self.assertRaises(ValueError):validate_link(value)
  self.assertEqual(validate_link(' https://example.org/a '),'https://example.org/a')
  service=DownloadService('tool','ffmpeg')
  fake=type('Result',(),{'returncode':0,'stdout':json.dumps({'title':'Movie <script>','formats':[{'height':1080,'vcodec':'h264'},{'height':720,'vcodec':'h264'},{'height':1080,'vcodec':'h264','has_drm':True}]})})()
  with patch('downloads.run_managed',return_value=fake):job=service.analyze('https://example.org/a')
  self.assertEqual(job['heights'],[1080,720]);self.assertEqual(job['title'],'Movie <script>')
  self.assertNotIn('url',job);self.assertNotIn('process',job)
  with self.assertRaises(ValueError):service.start(job['id'],'1080;command')
  with self.assertRaises(ValueError):service.completed_file(job['id'])
 def test_playback_keeps_split_streams_and_headers_private(self):
  service=DownloadService('tool','ffmpeg')
  info={'title':'Video','requested_formats':[{'vcodec':'av01','acodec':'none','url':'https://example.org/video','http_headers':{'User-Agent':'Browser','Referer':'https://example.org/','Bad':'hidden'}},{'vcodec':'none','acodec':'opus','url':'https://example.org/audio'}]}
  fake=type('Result',(),{'returncode':0,'stdout':json.dumps(info)})()
  with patch('downloads.run_managed',return_value=fake):job=service.analyze('https://example.org/watch')
  source,options=service.playback(job['id'])
  self.assertEqual(source,'https://example.org/video');self.assertEqual(options['audio_url'],'https://example.org/audio')
  self.assertIn('User-Agent: Browser',options['headers']);self.assertNotIn('Bad',options['headers'])
  self.assertNotIn('audio_url',job);self.assertNotIn('headers',job)
 def test_playlist_is_not_silently_downloaded(self):
  service=DownloadService('tool','ffmpeg');fake=type('Result',(),{'returncode':0,'stdout':'{"_type":"playlist","entries":[]}'})()
  with patch('downloads.run_managed',return_value=fake):
   with self.assertRaisesRegex(ValueError,'single video'):service.analyze('https://example.org/list')
 def test_cancel_targets_only_job_process(self):
  service=DownloadService('tool','ffmpeg');job={'id':'x','title':'Movie','duration':1,'heights':[],'state':'downloading','downloaded':0,'total':0,'speed':0,'eta':None,'error':'','process':object(),'cancel':False};service.jobs['x']=job
  with patch.object(service,'_stop') as stop:
   service.cancel('x');stop.assert_called_once_with(job['process'])
  self.assertTrue(job['cancel'])
 def test_movie_and_series_library_folders_include_year(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for category in ('Movies','Filmes'):
    with self.subTest(category=category):
     destination=root/category
     service=DownloadService('tool','ffmpeg',destination=destination)
     m_folder,m_tpl=service.build_target_folder_and_template({'title':'Paprika (2006)','info':{'title':'Paprika','release_year':2006}})
     self.assertEqual(m_folder,root/'Movies'/'Paprika (2006)')
     self.assertEqual(m_tpl,'Paprika (2006).%(ext)s')
     s_folder,s_tpl=service.build_target_folder_and_template({'title':'Severance S01E02 (2022)','info':{'title':'Severance','series':'Severance','release_year':2022,'season_number':1,'episode_number':2}})
     self.assertEqual(s_folder,root/'Series'/'Severance (2022)'/'Season 01')
     self.assertEqual(s_tpl,'Severance - S01E02.%(ext)s')

if __name__=='__main__':unittest.main()
