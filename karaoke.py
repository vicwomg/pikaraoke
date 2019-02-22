import urllib2
# from bs4 import BeautifulSoup
# from bs4 import SoupStrainer
import lxml.html
from pprint import pprint
import glob
import subprocess
import time
import os
import threading
import logging

class Karaoke:

    #paths
    download_path = "/home/pi/pikaraoke/songs"
    youtube_dl_path = "/usr/bin/youtube-dl"
    #vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
    player_path = "/usr/bin/omxplayer"

    queue = []
    available_songs = []
    now_playing = None
    process = None

    log_level = logging.DEBUG
    logging.basicConfig(format='%(levelname)s: %(message)s', level=log_level)

    def __init__(self):
        self.get_available_songs()

        if (not self.download_path.endswith('/')):
            self.download_path += '/'

    def get_search_results(self, textToSearch):
        logging.info("Searching YouTube for: " + textToSearch)
        query = urllib2.quote(textToSearch)
        url = "https://www.youtube.com/results?search_query=" + query
        response = urllib2.urlopen(url,None,10)
        if (response):
	        html = response.read()
	        # logging.debug("Straining for links...")
	#         strainer = SoupStrainer('a')
	#         soup = BeautifulSoup(html, 'lxml', parseOnlyThese=strainer)
	#         logging.debug("Scraping for class yt-uix-tile-link...")
	#         results = soup.findAll(attrs={'class':'yt-uix-tile-link'})
	        doc = lxml.html.fromstring(html)
	        elements = doc.xpath('//a[contains(@class,"yt-uix-tile-link")]')
	        results = []
	        for each in elements:
	            results.append({'title':each.get('title'), 'href':each.get('href')})
	        rc = []
	        for vid in results:
	            rc.append([vid['title'], 'https://www.youtube.com' + vid['href']])
	        return(rc)
        
        else:
        	logging.error("Failed to get response from: " + url)
        

    def get_karaoke_search_results(self, songTitle):
        return self.get_search_results(songTitle + " karaoke")

    def download_video(self, video_url, enqueue=False):
        logging.info("Downloading video: " + video_url)
        dl_path = self.download_path + "%(title)s---%(id)s.%(ext)s"
        cmd = [self.youtube_dl_path,
        	'-f', 'mp4',
        	'--external-downloader', 'aria2c',
        	'-R', '5',
        	"-o", dl_path, video_url]
        logging.debug("Youtube-dl command: " + ' '.join(cmd))
        rc = subprocess.call(cmd)
        if (rc != 0):
        	logging.error("Error code while downloading, retrying once...")
        	rc = subprocess.call(cmd) #retry once. Seems like this can be flaky
        if (rc == 0):
            logging.debug("Song successfully downloaded: " + video_url)
            self.get_available_songs()
            if (enqueue):
            	y = self.get_youtube_id_from_url(video_url)
            	s = self.find_song_by_youtube_id(y)
            	if (s):
            		self.enqueue(s)
            	else:
            		logging.error("Error queueing song: " + video_url)
        else:
        	logging.error("Error downloading song: " + video_url)
        return rc

    def get_available_songs(self):
        logging.debug("Fetching available songs in: " + self.download_path)
        self.available_songs = glob.glob(self.download_path + "*")
        return self.available_songs

    def filename_from_path(self, file_path):
	    rc = os.path.basename(file_path)
	    rc = os.path.splitext(rc)[0]
	    rc = rc.split("---")[0] #removes youtube id if present
	    return rc
	    
    def find_song_by_youtube_id(self, youtube_id):
	    for each in self.available_songs:
	    	if (youtube_id in each):
	    		return each
	    logging.error("No available song found with youtube id: " + youtube_id)
	    return None

    def get_youtube_id_from_url(self, url):
		s = url.split("watch?v=")
		if (len(s) == 2):
			return s[1]
		else:
			logging.error("Error parsing youtube id from url: " + url)
	    	return None

    def play_file(self, file_path):
        self.now_playing = self.filename_from_path(file_path)
        logging.info("Playing video: " + self.now_playing)
        # self.process = subprocess.Popen([self.vlc_path, "-I", "http"
        #     ,"--http-user=pi", "--http-password=pikaraoke", "--play-and-exit"
        #     ,file_path])
       #  self.process = subprocess.Popen([self.vlc_path, "--play-and-exit"
#             ,file_path])
        cmd = [self.player_path,file_path, "--blank"]
        logging.debug("Player command: " + ' '.join(cmd))
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE,)

    def is_file_playing(self):
        if (self.process.poll() == None):
            return True
        else:
            self.now_playing = None
            return False

    def enqueue(self, song_path):
        logging.info("Adding video to queue: " + song_path)
        self.queue.append(song_path)

    def skip(self):
        if (self.is_file_playing()):
            logging.info("Skipping: " + self.now_playing)
            self.process.stdin.write("q")
            self.now_playing = None
        else:
            logging.warning("Tried to skip, but no file is playing!")
            
    def pause(self):
        if (self.is_file_playing()):
            logging.info("Pausing: " + self.now_playing)
            self.process.stdin.write("p")
        else:
            logging.warning("Tried to pause, but no file is playing!")
            
    def vol_up(self):
        if (self.is_file_playing()):
            logging.info("Volume up: " + self.now_playing)
            self.process.stdin.write("=")
        else:
            logging.warning("Tried to volume up, but no file is playing!")
            
    def vol_down(self):
        if (self.is_file_playing()):
            logging.info("Volume down: " + self.now_playing)
            self.process.stdin.write("-")
        else:
            logging.warning("Tried to volume down, but no file is playing!")
            
    def restart(self):
        if (self.is_file_playing()):
            logging.info("Restarting: " + self.now_playing)
            self.process.stdin.write("i")
        else:
            logging.warning("Tried to restart, but no file is playing!")

    def run(self):
        logging.info("Starting Karaoke!")
        while True:
            if (len(self.queue) == 0):
                # wait for queue to contain something
                time.sleep(1)
            else:
                while (len(self.queue) > 0):
                    vid = self.queue[0]
                    self.play_file(vid)
                    while (self.is_file_playing()):
                        # wait for file to complete
                        time.sleep(0.5)
                    self.queue.pop(0)

# log_level = logging.DEBUG
# logging.basicConfig(format='%(levelname)s: %(message)s', level=log_level)
#
# k = Karaoke()
#
# t = threading.Thread(target=k.run)
# t.daemon = True
# t.start()
#
# songs = k.get_available_songs()
# for each in songs:
#     k.enqueue(each)
#
# results = k.get_karaoke_search_results('no doubt spiderwebs')
# pprint(results)
# vid = k.download_video(results[0][1])
# k.enqueue(vid)
#
# while True:
#     time.sleep(1)
